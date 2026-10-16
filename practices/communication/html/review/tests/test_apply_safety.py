from __future__ import annotations
import argparse
import contextlib
import io
import json
from pathlib import Path
from unittest.mock import patch
import unittest
import test_workbench as fixtures
from test_workbench import workbench, run

class ApplySafetyTest(unittest.TestCase):
    make_repo = fixtures.WorkbenchTest.make_repo
    # Inherit the fixture helper, not the unrelated suite.
    def setup_application(self):
        temp, repo, _ = self.make_repo(); self.addCleanup(temp.cleanup)
        proposal = repo / 'proposal.json'
        proposal.write_text(json.dumps({'files':[{'path':'alpha.md','proposedText':'new alpha\n'}, {'path':'beta.md','proposedText':'new beta\n'}]}))
        bundle_path=repo/'bundle.json'; response_path=repo/'response.json'
        run('reflection-bundle','--repo',str(repo),'--proposal',str(proposal),'--output',str(bundle_path))
        bundle=json.loads(bundle_path.read_text())
        response={'schemaVersion':'1.0','bundleId':bundle['bundleId'],'bundleDigest':bundle['bundleDigest'],'files':[{'path':'alpha.md','accepted':True,'desiredText':'new alpha\n'},{'path':'beta.md','accepted':True,'desiredText':'new beta\n'}],'comments':[],'decisions':[]}
        response_path.write_text(json.dumps(response))
        args=argparse.Namespace(repo=str(repo),bundle=str(bundle_path),response=str(response_path))
        return repo,bundle,response,response_path,args

    def test_malformed_response_never_changes_targets(self):
        repo,bundle,response,response_path,args=self.setup_application()
        baseline={name:(repo/name).read_bytes() for name in ['alpha.md','beta.md']}
        variants=[{'path':'alpha.md','accepted':True},{'path':'alpha.md','accepted':'false','desiredText':None},{'path':'alpha.md','accepted':1,'desiredText':None},None]
        for item in variants:
            with self.subTest(item=item):
                response['files']=[item];response_path.write_text(json.dumps(response))
                with self.assertRaises(workbench.WorkbenchError):workbench.command_apply(args)
                for name,data in baseline.items():self.assertEqual((repo/name).read_bytes(),data)

    def test_edit_during_staging_blocks_whole_write_set(self):
        repo,bundle,response,response_path,args=self.setup_application()
        original_beta=(repo/'beta.md').read_bytes()
        original_write=Path.write_bytes
        def change_then_stage(path,data):
            if path.suffix=='.review':(repo/'alpha.md').write_text('external edit\n')
            return original_write(path,data)
        with patch.object(Path,'write_bytes',change_then_stage):
            with self.assertRaises(workbench.WorkbenchError):workbench.command_apply(args)
        self.assertEqual((repo/'alpha.md').read_text(),'external edit\n')
        self.assertEqual((repo/'beta.md').read_bytes(),original_beta)

    def test_explicit_delete_is_still_supported(self):
        repo,bundle,response,response_path,args=self.setup_application()
        response['files']=[{'path':'alpha.md','accepted':True,'desiredText':None}]
        response_path.write_text(json.dumps(response))
        with contextlib.redirect_stdout(io.StringIO()):workbench.command_apply(args)
        self.assertFalse((repo/'alpha.md').exists())
        self.assertTrue((repo/'beta.md').exists())


    def test_mid_apply_conflict_reports_partial_write_and_preserves_external_edit(self):
        repo,bundle,response,response_path,args=self.setup_application()
        replace=workbench.os.replace
        def change_second_file(source,target):
            replace(source,target)
            if Path(target).name=='alpha.md':(repo/'beta.md').write_text('external beta\n')
        with patch.object(workbench.os,'replace',change_second_file):
            with self.assertRaises(workbench.WorkbenchError) as caught:workbench.command_apply(args)
        self.assertEqual((repo/'alpha.md').read_text(),'new alpha\n')
        self.assertEqual((repo/'beta.md').read_text(),'external beta\n')
        self.assertIn("applied files: ['alpha.md']",str(caught.exception))
        backup=Path(str(caught.exception).split('recovery copy: ',1)[1])
        self.assertEqual((backup/'1').read_text(),'# Beta\n')
        self.assertEqual(json.loads((backup/'manifest.json').read_text())['files'][1]['path'],'beta.md')
