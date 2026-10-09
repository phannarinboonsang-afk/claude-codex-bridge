"""Offline Git source handoff; does not claim runtime sandbox acceptance."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

loader=importlib.util.spec_from_file_location('handoff_runner',Path(__file__).resolve().parents[2]/'provisioning/digitalocean/runner.py')
runner=importlib.util.module_from_spec(loader);loader.loader.exec_module(runner)


class HandoffTests(unittest.TestCase):
    def test_commit_pinned_clone_preserves_previous_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);previous=root/'previous';previous.mkdir()
            git=['git','-C',str(previous),'-c','core.hooksPath=/dev/null','-c','user.name=Synthetic Worker','-c','user.email=worker@example.invalid']
            subprocess.run(git+['init','-q'],check=True)
            (previous/'source.txt').write_text('base')
            subprocess.run(git+['add','--all'],check=True);subprocess.run(git+['commit','-qm','base'],check=True)
            base=subprocess.check_output(git+['rev-parse','HEAD'],text=True).strip()
            (previous/'source.txt').write_text('round one')
            subprocess.run(git+['add','--all'],check=True);subprocess.run(git+['commit','-qm','round one'],check=True)
            head=subprocess.check_output(git+['rev-parse','HEAD'],text=True).strip()
            registration={'base_commit':base,'repository_url':previous.as_uri(),'previous_run_id':'a'*32,'previous_commit':head}
            destination=root/'next'
            runner.checkout_source(registration,['git','-C',str(root)],str(destination),previous.as_uri())
            self.assertEqual((destination/'source.txt').read_text(),'round one')
            self.assertEqual(subprocess.check_output(['git','-C',str(destination),'rev-parse','HEAD'],text=True).strip(),head)

    def test_non_ancestor_commit_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);previous=root/'previous';previous.mkdir()
            git=['git','-C',str(previous),'-c','user.name=Synthetic Worker','-c','user.email=worker@example.invalid']
            subprocess.run(git+['init','-q'],check=True);(previous/'source.txt').write_text('base')
            subprocess.run(git+['add','--all'],check=True);subprocess.run(git+['commit','-qm','base'],check=True)
            head=subprocess.check_output(git+['rev-parse','HEAD'],text=True).strip()
            with self.assertRaises(RuntimeError):
                runner.checkout_source({'base_commit':'f'*40,'previous_run_id':'a'*32,'previous_commit':head},['git','-C',str(root)],str(root/'next'),previous.as_uri())


if __name__=='__main__':unittest.main()
