import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]


def module(name):
    spec=importlib.util.spec_from_file_location('monitor_'+name,ROOT/'scripts'/(name+'.py'))
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result


class Ports(unittest.TestCase):
    def test_existing_cloud_port_opens_only_same_checkout(self):
        m=module('launch')
        with patch.object(m,'health',return_value={'app':'branch-monitor','directory':str(m.ROOT)}),patch.object(m.webbrowser,'open') as browser,contextlib.redirect_stdout(io.StringIO()):
            m.main(['--port','3001','--browser-host','localhost'])
            browser.assert_called_once_with('http://localhost:3001')

    def test_existing_other_checkout_not_opened(self):
        m=module('launch')
        with patch.object(m,'health',return_value={'app':'branch-monitor','directory':'/different'}),patch.object(m.webbrowser,'open') as browser,self.assertRaises(SystemExit):
            m.main(['--port','3001','--browser-host','localhost'])
        browser.assert_not_called()

    def test_legacy_default_unchanged(self):
        m=module('launch')
        with patch.object(m,'health',return_value={'app':'branch-monitor','directory':str(m.ROOT)}),patch.object(m.webbrowser,'open') as browser,contextlib.redirect_stdout(io.StringIO()):
            m.main([])
            browser.assert_called_once_with('http://127.0.0.1:8765')

    def test_backend_receives_selected_port(self):
        m=module('launch')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'dist').mkdir();(root/'dist/index.html').write_text('test')
            good={'app':'branch-monitor','directory':str(root)}
            with patch.object(m,'ROOT',root),patch.object(m,'health',side_effect=[None,good]),patch.object(m.importlib.util,'find_spec',return_value=True),patch.object(m.subprocess,'Popen') as proc,patch.object(m.webbrowser,'open'),contextlib.redirect_stdout(io.StringIO()):
                proc.return_value.poll.return_value=None
                m.main(['--port','3001','--browser-host','localhost'])
                self.assertEqual(proc.call_args.args[0][-2:],['--port','3001'])

    def test_venv_relaunch_preserves_options(self):
        m=module('launch')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'.venv/bin').mkdir(parents=True);(root/'.venv/bin/python').touch()
            with patch.object(m,'ROOT',root),patch.object(m,'health',return_value=None),patch.object(m.importlib.util,'find_spec',return_value=None),patch.object(m.subprocess,'run'),patch.object(m.os,'execv',side_effect=SystemExit) as again,self.assertRaises(SystemExit):
                m.main(['--port','3001','--browser-host','localhost'])
            self.assertEqual(again.call_args.args[1][-4:],['--port','3001','--browser-host','localhost'])

    def test_stop_only_same_checkout_at_selected_port(self):
        m=module('stop');response=io.StringIO('{"app":"branch-monitor","directory":'+__import__('json').dumps(str(m.ROOT))+',"pid":12345}')
        with patch.object(m.urllib.request,'urlopen',return_value=response) as request,patch.object(m.os,'kill') as kill,contextlib.redirect_stdout(io.StringIO()):
            m.main(['--port','3001','--browser-host','localhost'])
            self.assertEqual(request.call_args.args[0],'http://localhost:3001/api/health')
            kill.assert_called_once_with(12345,m.signal.SIGTERM)

    def test_stop_refuses_other_checkout(self):
        m=module('stop');response=io.StringIO('{"app":"branch-monitor","directory":"/other","pid":12345}')
        with patch.object(m.urllib.request,'urlopen',return_value=response),patch.object(m.os,'kill') as kill,self.assertRaises(SystemExit):
            m.main(['--port','3001'])
        kill.assert_not_called()


if __name__=='__main__':unittest.main(verbosity=2)
