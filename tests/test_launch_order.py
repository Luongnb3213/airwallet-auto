import contextlib
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import driver
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import airwallet_dom_runner as runner


class LaunchOrderTests(unittest.TestCase):
    def make_device(self, running_pid=0):
        events=[]
        device,session,script=Mock(),Mock(),Mock()
        device.enumerate_applications.return_value=[SimpleNamespace(identifier=driver.BUNDLE,pid=running_pid)]
        device.spawn.side_effect=lambda args:(events.append('spawn') or 42)
        device.resume.side_effect=lambda pid:events.append('resume')
        device.attach.side_effect=lambda pid:(events.append('attach') or session)
        session.create_script.return_value=script
        script.load.side_effect=lambda:events.append('load')
        return device,session,script,events

    def test_cold_app_resumes_before_dyld_attach(self):
        device,session,script,events=self.make_device()
        with patch.object(driver.frida,'get_device',return_value=device), \
             patch.object(driver,'load_agent_source',return_value=''), \
             patch.object(driver.time,'sleep'):
            result=driver.connect('test-device',0)
        self.assertEqual(events,['spawn','resume','attach','load'])
        self.assertIs(result[1],session)

    def test_running_app_attaches_without_spawn_or_resume(self):
        device,session,script,events=self.make_device(running_pid=77)
        with patch.object(driver.frida,'get_device',return_value=device), \
             patch.object(driver,'load_agent_source',return_value=''), \
             patch.object(driver.time,'sleep'):
            driver.connect('test-device',0)
        self.assertEqual(events,['attach','load'])
        device.attach.assert_called_once_with(77)

    def test_script_failure_detaches_without_resuming_twice(self):
        device,session,script,events=self.make_device()
        script.load.side_effect=RuntimeError('load failed')
        with patch.object(driver.frida,'get_device',return_value=device), \
             patch.object(driver,'load_agent_source',return_value=''), \
             patch.object(driver.time,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'load failed'):
                driver.connect('test-device',0)
        device.resume.assert_called_once_with(42)
        session.detach.assert_called_once()

    def test_dom_cold_launch_also_resumes_before_attach(self):
        device,session,script,events=self.make_device()
        args=runner.build_parser().parse_args(['--spawn-attempts','1'])
        with patch.object(runner,'spawn_slot',return_value=contextlib.nullcontext()), \
             patch.object(runner.time,'sleep'), contextlib.redirect_stderr(io.StringIO()):
            actual,spawned=runner.spawn_attach_resume(device,args,[])
        self.assertIs(actual,session)
        self.assertTrue(spawned)
        self.assertEqual(events,['spawn','resume','attach'])


if __name__=='__main__':unittest.main()
