import copy,json,tempfile,unittest,subprocess,sys
from pathlib import Path
from PIL import Image
from unittest.mock import patch
from workspaces import WorkspaceRegistry
from workflow import write,WorkflowError
from personal_library import PersonalLibrary
from editor_bridge import EditorBridge

class StudioV7Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        write(self.root/'data/project.json',{'id':'root','title':'test','shots':[]})
        self.registry=WorkspaceRegistry(self.root);self.a=self.registry.create({'name':'A','activate':False});self.b=self.registry.create({'name':'B','activate':False})
        self.library=PersonalLibrary(self.root);self.person=self.library.person({'name':'测试资料组'})
        self.source=self.root/'source.png';Image.new('RGB',(32,32),'navy').save(self.source)
    def add(self,pid=None):return self.library.add({'personId':pid or self.person['id'],'path':str(self.source),'name':'测试形象','role':'identity'})
    def test_personal_media_can_be_reused_without_cross_workspace_mutation(self):
        original=self.source.read_bytes();item=self.add()
        one=self.library.use(self.a,{'assetId':item['id']});two=self.library.use(self.b,{'assetId':item['id']})
        self.assertNotEqual(one['path'],two['path']);self.assertTrue(Path(one['path']).is_relative_to(self.a.media));self.assertTrue(Path(two['path']).is_relative_to(self.b.media))
        self.library.edit_asset(item['id'],{'name':'新名称','reviewStatus':'blocked'})
        self.assertEqual(self.a.store.assets()['assets'][0]['name'],'测试形象');self.assertEqual(self.source.read_bytes(),original)
    def test_same_file_assigned_to_distinct_people_keeps_identity(self):
        first=self.add();person=self.library.person({'name':'另一人'});second=self.add(person['id'])
        self.assertNotEqual(first['id'],second['id']);self.assertEqual(first['path'],second['path'])
        imported=self.library.use(self.a,{'assetId':second['id']});self.assertEqual(imported['characterId'],person['id'])
    def test_invalid_person_and_upload_types_rejected(self):
        with self.assertRaises(WorkflowError):self.library.add({'personId':'unknown','path':str(self.source)})
        with self.assertRaises(WorkflowError):self.library.add({'filename':'bad.exe','dataBase64':'YWJj'})
    def test_old_timeline_is_preserved_during_seed(self):
        item=self.library.use(self.a,{'assetId':self.add()['id']})
        old={'revision':7,'settings':{'width':1280,'height':720,'fps':24},'clips':[{'id':'c','assetId':item['id'],'in':0,'out':2}],'audio':[],'subtitles':[]}
        write(self.a.data/'timeline.json',old);bridge=EditorBridge(self.root);bridge.seed(self.a)
        self.assertEqual(json.loads((self.a.data/'timeline.json').read_text()),old)
        new=json.loads((bridge.directory(self.a)/'project.json').read_text());self.assertEqual(new['clips'][0]['duration'],2);self.assertEqual(new['media'][0]['studioAssetId'],item['id'])
    def test_editor_media_cannot_borrow_another_workspace(self):
        bridge=EditorBridge(self.root);item=self.library.use(self.a,{'assetId':self.add()['id']})
        with self.assertRaises(WorkflowError):bridge.copy_asset(self.b,item)
        bridge.directory(self.a).mkdir(parents=True);(bridge.directory(self.a)/'exports').mkdir();(bridge.directory(self.a)/'exports/film.mp4').write_bytes(b'test')
        with self.assertRaises(WorkflowError):bridge.media_path(self.b,'film.mp4','exports')
    def test_caption_position_and_reimport_preserve_manual_titles(self):
        bridge=EditorBridge(self.root);doc={'revision':4,'width':1280,'height':720,'clips':[{'id':'manual','props':{'text':'手工标题'}}]};job={'id':'j','status':'succeeded','transcript':{'segments':[{'start':0,'end':1,'text':'字幕'}]}}
        saved=[]
        with patch.object(bridge,'project',return_value=copy.deepcopy(doc)),patch.object(bridge,'save',side_effect=lambda w,d:saved.append(d)):
            result=bridge.apply_captions(self.a,{'jobId':'j','revision':4,'position':'top','replaceAuto':True},[job])
            self.assertEqual(result['clips'][0]['id'],'manual');self.assertLess(result['clips'][1]['props']['y'],0)
            with self.assertRaises(WorkflowError):bridge.apply_captions(self.a,{'jobId':'j','revision':3},[job])

    def test_encoded_export_filename_is_recovered_with_current_workspace(self):
        bridge=EditorBridge(self.root);target=bridge.directory(self.a)/'exports'/'剪辑 成片.mp4'
        target.parent.mkdir(parents=True);target.write_bytes(b'encoded-name-test')
        self.assertEqual(bridge.media_path(self.a,'/exports/%E5%89%AA%E8%BE%91%20%E6%88%90%E7%89%87.mp4','exports'),target)
        with self.assertRaises(WorkflowError):bridge.media_path(self.a,'/exports/%2e%2e%2fprivate.mp4','exports')
