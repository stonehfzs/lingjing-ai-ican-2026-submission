"""Real FFmpeg editing in isolated workspaces; no paid provider requests."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from editing import LocalJobs, empty_timeline, validate_timeline, save_timeline, render_timeline, process_audio, resolve_media
from media_probe import binary, probe
from workflow import write, WorkflowError
from workspaces import WorkspaceRegistry


class EditingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not binary('ffmpeg'):raise unittest.SkipTest('FFmpeg unavailable')
        cls.tmp=tempfile.TemporaryDirectory();cls.root=Path(cls.tmp.name)
        write(cls.root/'data/project.json',{'id':'test','title':'test','shots':[]})
        cls.registry=WorkspaceRegistry(cls.root)
        cls.w=cls.registry.create({'name':'Edit test','activate':False})
        video=cls.w.media/'source.mp4';audio=cls.w.media/'line.wav'
        subprocess.run([binary('ffmpeg'),'-v','error','-y','-f','lavfi','-i','color=c=blue:s=320x240:r=24:d=3','-an','-c:v','libx264',str(video)],check=True)
        subprocess.run([binary('ffmpeg'),'-v','error','-y','-f','lavfi','-i','sine=frequency=440:duration=3','-ac','2',str(audio)],check=True)
        cls.v=cls.w.store.import_asset({'path':str(video),'kind':'video','name':'blue'})['id']
        cls.a=cls.w.store.import_asset({'path':str(audio),'kind':'audio','name':'tone'})['id']
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def timeline(self):
        t=empty_timeline(self.w);t['clips']=[{'id':'c1','assetId':self.v,'in':.25,'out':1.25,'mute':True},
            {'id':'c2','assetId':self.v,'in':1.25,'out':2.25,'mute':True}]
        t['audio']=[{'id':'a1','assetId':self.a,'in':0,'out':1.5,'at':.25,'gain':.3}]
        t['subtitles']=[{'start':.1,'end':1.5,'text':'测试字幕'}]
        return t
    def test_source_is_immutable_and_real_export_has_mixed_audio(self):
        before=(self.w.media/'source.mp4').read_bytes()
        outputs,meta=render_timeline(self.w,self.timeline(),self.w.media/'render-test')
        info=probe(outputs[0][0]);self.assertAlmostEqual(info['duration'],2,delta=.15)
        self.assertTrue(any(s['codec_type']=='audio' for s in info['streams']))
        self.assertGreater(max(info['waveform']),0)
        self.assertIn('测试字幕',Path(meta['subtitlePath']).read_text(encoding='utf-8-sig'))
        self.assertEqual((self.w.media/'source.mp4').read_bytes(),before)
    def test_bad_trim_nan_duplicate_ids_and_audio_reference_rejected(self):
        for change in [lambda t:t['clips'][0].update({'out':9}),lambda t:t['clips'][0].update({'in':float('nan')}),
                       lambda t:t['clips'][1].update({'id':'c1'}),lambda t:t['audio'][0].update({'assetId':self.v})]:
            t=self.timeline();change(t)
            with self.assertRaises(WorkflowError):validate_timeline(self.w,t)
    def test_cannot_borrow_asset_from_other_workspace(self):
        other=self.registry.create({'name':'Isolated','activate':False})
        with self.assertRaises(WorkflowError):resolve_media(other,self.v,{'video'})
    def test_save_conflicts_do_not_overwrite(self):
        t=self.timeline();save_timeline(self.w,t)
        with self.assertRaises(WorkflowError):save_timeline(self.w,t)
    def test_idempotent_local_job_and_no_audio_stream(self):
        jobs=LocalJobs(self.root);spec={'requestId':'local-test-id','operation':'extract_audio','assetId':self.a}
        first,fresh=jobs.prepare(self.w,spec);second,fresh2=jobs.prepare(self.w,spec)
        self.assertTrue(fresh);self.assertFalse(fresh2);self.assertEqual(first['id'],second['id'])
        with self.assertRaises(WorkflowError):jobs.prepare(self.w,{**spec,'out':1})
        with self.assertRaises(WorkflowError):jobs.prepare(self.w,{'requestId':'no-audio-test','operation':'extract_audio','assetId':self.v})
        jobs.execute(self.w,first['id']);result=jobs.get(self.w)[0]
        self.assertEqual(result['status'],'succeeded');self.assertEqual(result['assets'][0]['mediaType'],'audio')

    def test_transcript_import_preserves_manual_subtitles_and_isolated_job(self):
        from editing import import_transcript
        t=self.timeline();t['revision']=0
        # Every test import uses a separate timeline namespace, regardless of test ordering.
        path=self.w.data/'timeline.json'
        old=json.loads(path.read_text(encoding='utf8')) if path.exists() else None
        if old:t['revision']=old['revision']
        saved=save_timeline(self.w,t)
        job={'id':'asr-test','workspaceId':self.w.id,'status':'succeeded','transcript':{'segments':[{'start':.5,'end':4,'text':'识别稿'}]}}
        first=import_transcript(self.w,job,saved['revision'])
        self.assertEqual(len(first['subtitles']),2);self.assertEqual(first['subtitles'][1]['end'],2)
        second=import_transcript(self.w,job,first['revision'],.1)
        self.assertEqual(len(second['subtitles']),2);self.assertEqual(second['subtitles'][0]['text'],'测试字幕')
        with self.assertRaises(WorkflowError):import_transcript(self.w,job,first['revision'])
        with self.assertRaises(WorkflowError):import_transcript(self.w,{**job,'workspaceId':'other'},second['revision'])
