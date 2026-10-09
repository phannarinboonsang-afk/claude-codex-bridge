import ast
import json
import pathlib
import re
import unittest
import jsonschema
from test_security import spec,policy
from worker import unit_text
from provision import cloud_init

ROOT=pathlib.Path(__file__).resolve().parents[1]

class StaticTests(unittest.TestCase):
    def test_python_syntax(self):
        for p in ROOT.rglob('*.py'):
            with self.subTest(file=p.name):ast.parse(p.read_text(encoding='utf8'))
    def test_schema_valid(self):
        for p in (ROOT/'config').glob('*.schema.json'):
            with self.subTest(file=p.name):jsonschema.Draft202012Validator.check_schema(json.loads(p.read_text()))
    def test_bootstrap_supplies_pytest(self):
        self.assertIn("'python3-pytest'",(ROOT/'guest/bootstrap.py').read_text())
    def test_run_schema(self):
        schema=json.loads((ROOT/'config/run-spec.schema.json').read_text());jsonschema.validate(spec(),schema)
        r=spec();r['pid']=1
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(r,schema)
    def test_cloud_init_all_files_present(self):
        c=json.loads(cloud_init().split('\n',1)[1]);self.assertGreaterEqual(len(c['write_files']),12)
        self.assertEqual(c['runcmd'],[['/usr/bin/python3','/opt/agentbridge-worker/bootstrap.py']])
        self.assertLess(len(cloud_init().encode()),65536)
    def test_cloud_init_compression_roundtrip(self):
        import gzip,base64
        files=json.loads(cloud_init().split('\n',1)[1])['write_files']
        item=next(x for x in files if x['path']=='/opt/agentbridge-worker/worker.py')
        self.assertEqual(gzip.decompress(base64.b64decode(item['content'])),(ROOT/'worker.py').read_bytes())
    def test_generated_unit_sections(self):
        text=unit_text(policy().validate(spec()))
        self.assertEqual(text.count('[Service]'),1);self.assertIn('WorkingDirectory=/srv/agentbridge/runs/',text)
        self.assertNotIn('sudo',text);self.assertNotIn('User=root',text)
    def test_no_shell_subprocess(self):
        for p in ROOT.rglob('*.py'):
            tree=ast.parse(p.read_text())
            for n in ast.walk(tree):
                if isinstance(n,ast.Call):
                    for k in n.keywords:
                        if k.arg=='shell':self.assertNotEqual(getattr(k.value,'value',False),True,str(p))
    def test_no_private_keys_or_provider_tokens(self):
        patterns=[r'-----BEGIN (?:OPENSSH|RSA|EC|DSA) PRIVATE KEY-----',r'\bdop_v1_[A-Za-z0-9]{20,}',r'\bsk-(?:proj-|ant-)[A-Za-z0-9_-]{20,}',r'(?i)authorization[\"\']?\s*[:=]\s*[\"\']?bearer\s+[A-Za-z0-9_-]{24,}',r'(?i)bridge[_-]bearer[\"\']?\s*[:=]\s*[\"\'][A-Za-z0-9_-]{16,}']
        for p in ROOT.rglob('*'):
            if not p.is_file() or '__pycache__' in p.parts or '.git' in p.parts:continue
            text=p.read_text(encoding='utf8')
            for pattern in patterns:self.assertIsNone(re.search(pattern,text),str(p))
    def test_source_not_spawned_by_root_controller(self):
        tree=ast.parse((ROOT/'worker.py').read_text())
        prepare=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='prepare')
        self.assertFalse(any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='run' for n in ast.walk(prepare)))
    def test_bwrap_source_inside_resource_boundary(self):
        text=(ROOT/'runner.py').read_text();self.assertIn("git+['clone'",text)
        self.assertIn("'--ro-bind','/opt/agentbridge-tools'",text)
    def test_root_state_not_mounted_in_sandbox(self):
        tree=ast.parse((ROOT/'runner.py').read_text())
        literals=[n.value for n in ast.walk(tree) if isinstance(n,ast.Constant) and isinstance(n.value,str)]
        self.assertNotIn('/var/lib/agentbridge-worker',literals)
        self.assertNotIn('/root',literals)

if __name__=='__main__':unittest.main()
