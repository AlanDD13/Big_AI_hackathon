import tempfile
import unittest
from unittest.mock import patch

from robot.sdk_owner import sdk_owner


class OwnerTests(unittest.TestCase):
    def test_only_one_local_sdk_owner_and_exception_releases(self):
        with tempfile.TemporaryDirectory() as directory, patch('robot.sdk_owner.tempfile.gettempdir', return_value=directory):
            with self.assertRaisesRegex(ValueError, 'test fault'):
                with sdk_owner('3JKCK7W0030DJS'):
                    with self.assertRaisesRegex(RuntimeError, 'owns the rover SDK'):
                        with sdk_owner('3JKCK7W0030DJS'):
                            self.fail('competing SDK session')
                    raise ValueError('test fault')
            with sdk_owner('3JKCK7W0030DJS'):
                pass
