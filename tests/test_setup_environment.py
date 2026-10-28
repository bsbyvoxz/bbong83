from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import setup_environment as setup


class SetupTests(unittest.TestCase):
    def test_compatible_version_and_architecture(self):
        for version in ((3,10,0),(3,11,8),(3,12,10),(3,13,2)):
            self.assertTrue(setup.supported_runtime(version,64))
        for version,bits in (((3,9,9),64),((3,14,0),64),((3,12,0),32)):
            self.assertFalse(setup.supported_runtime(version,bits))

    def test_real_environment_can_be_created_and_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)/'.venv'
            python=setup.prepare_environment(directory)
            self.assertTrue(setup.environment_works(python))
            self.assertEqual(setup.prepare_environment(directory),python)
            self.assertEqual(list(Path(tmp).glob('.venv.previous-*')),[])

    def test_broken_environment_is_preserved_before_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)/'.venv';directory.mkdir()
            (directory/'user-marker.txt').write_text('preserve this')
            python=setup.prepare_environment(directory)
            backups=list(Path(tmp).glob('.venv.previous-*'))
            self.assertEqual(len(backups),1)
            self.assertEqual((backups[0]/'user-marker.txt').read_text(),'preserve this')
            self.assertTrue(setup.environment_works(python))

    def test_child_command_failure_is_propagated_and_stops_tests(self):
        with patch.object(setup,'prepare_environment',return_value=Path('/test/python')):
            with patch.object(setup.subprocess,'run') as run:
                run.return_value.returncode=7
                self.assertEqual(setup.main(),7)
                self.assertEqual(run.call_count,1)


if __name__=='__main__':unittest.main()
