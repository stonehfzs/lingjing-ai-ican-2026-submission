import copy,subprocess,unittest
from unittest.mock import patch
from tests import test_editor_v7 as base
from editor_bridge import EditorBridge
from workflow import WorkflowError
from media_probe import binary,probe
class EditorProcessingTests(unittest.TestCase):
    def setUp(self):
        base.StudioV7Tests.setUp(self);self.bridge=EditorBridge(self.root);source=self.root/'source.mp4'
        subprocess.run([binary('ffmpeg'),'-v','error','-y','-f','lavfi','-i','color=c=green:s=64x64:r=24:d=2','-f','lavfi','-i','sine=frequency=440:duration=2','-c:v','libx264','-c:a','aac','-shortest',str(source)],check=True)
        asset=self.a.store.import_asset({'path':str(source),'name':'clip','role':'reference'});media=self.bridge.copy_asset(self.a,asset)
        self.doc={'revision':7,'media':[media],'clips':[{'id':'c','mediaId':asset['id'],'kind':'video','track':'V1','start':5,'in':.5,'duration':.75,'props':{},'keyframes':None}]}
    def test_model_receives_trimmed_audio_and_original_context(self):
        with patch.object(self.bridge,'project',return_value=self.doc):
            spec,context=self.bridge.prepare_clip_source(self.a,{'operation':'voice_convert','revision':7,'clipId':'c','requestId':'trim-test-1','referenceAssetId':'reference'})
        asset=next(a for a in self.a.store.assets()['assets'] if a['id']==spec['assetId']);info=probe(asset['path'],False)
        self.assertAlmostEqual(info['duration'],.75,places=2);self.assertEqual(context['start'],5);self.assertEqual(spec['referenceAssetId'],'reference');self.assertEqual(asset['mediaType'],'audio')
    def test_stale_or_retimed_clip_rejected(self):
        with patch.object(self.bridge,'project',return_value=self.doc):
            with self.assertRaises(WorkflowError):self.bridge.prepare_clip_source(self.a,{'revision':6,'operation':'extract_audio','clipId':'c'})
            self.doc['clips'][0]['props']['speed']=2
            with self.assertRaises(WorkflowError):self.bridge.prepare_clip_source(self.a,{'revision':7,'operation':'extract_audio','clipId':'c'})
    def test_lipsync_uses_trimmed_video_and_keeps_reference(self):
        with patch.object(self.bridge,'project',return_value=self.doc):
            spec,_=self.bridge.prepare_clip_source(self.a,{'revision':7,'clipId':'c','operation':'lip_sync','referenceAssetId':'audio','requestId':'lip-trim-test'})
        asset=next(a for a in self.a.store.assets()['assets'] if a['id']==spec['assetId']);self.assertEqual(asset['mediaType'],'video');self.assertAlmostEqual(probe(asset['path'],False)['duration'],.75,places=2)

    def test_clip_subtitles_replace_only_overlapping_auto_captions(self):
        doc=copy.deepcopy(self.doc);doc.update(width=640,height=360)
        doc['clips'] += [{'id':'old','start':5.1,'duration':.3,'props':{'studioCaptionBatch':'old'}},{'id':'elsewhere','start':0,'duration':1,'props':{'studioCaptionBatch':'old'}},{'id':'manual','start':5.1,'duration':.3,'props':{'text':'title'}}]
        job={'id':'asr','status':'succeeded','editorClip':copy.deepcopy(doc['clips'][0]),'transcript':{'segments':[{'start':0,'end':.5,'text':'字幕'}]}}
        with patch.object(self.bridge,'project',return_value=doc),patch.object(self.bridge,'save'):
            result=self.bridge.apply_captions(self.a,{'revision':7,'jobId':'asr','offset':5,'replaceClipAuto':True},[job])
        ids={c['id'] for c in result['clips']};self.assertNotIn('old',ids);self.assertIn('elsewhere',ids);self.assertIn('manual',ids)
