import argparse
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from deploy.backup_sqlite import backup
from deploy.oci_free_vm import inventory, provision


class BackupTests(unittest.TestCase):
    def test_backup_includes_committed_wal_data_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            source, dest = Path(directory) / 'db.sqlite3', Path(directory) / 'backup.sqlite3'
            with sqlite3.connect(source) as connection:
                connection.execute('PRAGMA journal_mode=WAL')
                connection.execute('CREATE TABLE sample (value TEXT)')
                connection.execute("INSERT INTO sample VALUES ('saved')")
                connection.commit()
                backup(source, dest)
            with sqlite3.connect(dest) as connection:
                self.assertEqual(connection.execute('SELECT value FROM sample').fetchone(), ('saved',))
            with self.assertRaises(FileExistsError):
                backup(source, dest)


class ProvisionTests(unittest.TestCase):
    def args(self, **overrides):
        values = dict(region='home', tenancy='tenancy', ssh_cidr='203.0.113.1/32',
                      ssh_public_key='key.pub', ad=None, apply=False, confirm_free_account=False)
        return argparse.Namespace(**(values | overrides))

    @patch('deploy.oci_free_vm.cli')
    def test_wrong_region_stops_before_creating_anything(self, cli):
        cli.return_value = [{'is-home-region': True, 'region-name': 'elsewhere'}]
        with self.assertRaisesRegex(SystemExit, 'home region'):
            inventory(self.args())
        self.assertEqual(cli.call_count, 1)

    @patch('deploy.oci_free_vm.cli')
    def test_existing_boot_volume_stops(self, cli):
        cli.side_effect = [[{'is-home-region': True, 'region-name': 'home'}],
                           [{'name': 'ad'}], [], [], [], [{'lifecycle-state': 'AVAILABLE'}]]
        with self.assertRaisesRegex(SystemExit, 'boot volumes'):
            inventory(self.args())

    @patch('deploy.oci_free_vm.cli')
    def test_preview_only_reads_and_apply_requires_free_account_confirmation(self, cli):
        cli.side_effect = lambda args, *cmd: (
            [{'is-home-region': True, 'region-name': 'home'}] if cmd[:2] == ('iam', 'region-subscription')
            else [{'name': 'ad'}] if cmd[:2] == ('iam', 'availability-domain')
            else [{'id': 'image', 'compartment-id': None, 'lifecycle-state': 'AVAILABLE'}]
            if cmd[:2] == ('compute', 'image') else []
        )
        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            try:
                os.chdir(directory)
                Path('key.pub').write_text('ssh-ed25519 PUBLIC-TEST-KEY')
                provision(self.args(region=None))
                self.assertFalse(Path('.oci-state.json').exists())
                with self.assertRaisesRegex(SystemExit, 'verify Free Trial'):
                    provision(self.args(apply=True))
                self.assertFalse(Path('.oci-state.json').exists())
                self.assertTrue(all('create' not in call.args and 'launch' not in call.args
                                    for call in cli.call_args_list))
            finally:
                os.chdir(previous)

    @patch('deploy.oci_free_vm.cli')
    def test_apply_creates_only_fixed_vm_and_journal_blocks_duplicate_run(self, cli):
        def response(args, *command):
            if command[:2] == ('iam', 'region-subscription'):
                return [{'is-home-region': True, 'region-name': 'home'}]
            if command[:2] == ('iam', 'availability-domain'):
                return [{'name': 'ad'}]
            if command[:2] == ('compute', 'image'):
                return [{'id': 'image', 'compartment-id': None, 'lifecycle-state': 'AVAILABLE'}]
            if 'create' in command or 'launch' in command:
                return {'id': 'id-' + command[1]}
            if 'list-vnics' in command:
                return [{'public-ip': '203.0.113.2'}]
            return []

        cli.side_effect = response
        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            try:
                os.chdir(directory)
                Path('key.pub').write_text('ssh-ed25519 PUBLIC-TEST-KEY')
                provision(self.args(apply=True, confirm_free_account=True))
                launch = next(call.args for call in cli.call_args_list if 'launch' in call.args)
                self.assertEqual(launch[launch.index('--shape') + 1], 'VM.Standard.A1.Flex')
                self.assertEqual(launch[launch.index('--shape-config') + 1], '{"ocpus":1,"memoryInGBs":6}')
                self.assertEqual(launch[launch.index('--boot-volume-size-in-gbs') + 1], '50')
                cli.reset_mock()
                with self.assertRaisesRegex(SystemExit, 'already exists'):
                    provision(self.args(apply=True, confirm_free_account=True))
                cli.assert_not_called()
            finally:
                os.chdir(previous)

    def test_public_ssh_is_rejected_before_api_calls(self):
        with self.assertRaisesRegex(SystemExit, 'single public IPv4'):
            provision(self.args(ssh_cidr='0.0.0.0/0'))


if __name__ == '__main__':
    unittest.main()
