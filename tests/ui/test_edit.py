"""명세 직접 수정·배치 역변환·동시 수정 보호·수정 기록을 검증한다."""
import hashlib
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

pytest.importorskip("starlette")
from starlette.testclient import TestClient
from src.ui.server import create_app

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def editing(tmp_path):
    raw = json.loads((ROOT / 'tests/engine/specs/dangsan_typical.json').read_text(encoding='utf8'))
    (tmp_path / 'plan.json').write_text(json.dumps(raw, ensure_ascii=False), encoding='utf8')
    return tmp_path, TestClient(create_app(tmp_path))


def read(c, path='plan.json'):
    return c.get('/api/spec', params={'path': path}).json()


def edit(c, ops, path='plan.json', sha=None):
    return c.post('/api/spec/edit', json={'path': path, 'sha256': sha or read(c, path).get('sha256', ''), 'ops': ops})


def test_set_changes_ten_bathrooms(editing):
    work, c = editing
    old = read(c)
    response = edit(c, [{'op': 'set', 'target': 'items[2].items[1]', 'field': 'points', 'index': 1, 'value': [1700, 3800]}])
    assert response.status_code == 200
    data = response.json()
    assert data['sha256'] != old.get('sha256')
    assert data['sha256'] == hashlib.sha256((work / 'plan.json').read_bytes()).hexdigest()
    baths = [p for p in data['prims'] if p['sources'][0]['item'] == 'items[2].items[1]']
    assert len(baths) == 10
    assert all(p['points'][1][0] - p['points'][0][0] == 1700 for p in baths)
    assert json.loads((work / 'plan.json').read_text(encoding='utf8'))['items'][2]['items'][1]['points'][1] == [1700, 3800]


def test_place_and_grid_in_order(editing):
    _, c = editing
    response = edit(c, [
        {'op': 'set_place', 'group': 'items[2]', 'place': 0, 'field': 'rotation', 'value': 90},
        {'op': 'set_place', 'group': 'items[2]', 'place': 0, 'field': 'repeat.count', 'value': 2},
        {'op': 'set_place', 'group': 'items[2]', 'place': 0, 'field': 'vars.no.start', 'value': 401},
        {'op': 'set_grid', 'axis': 'X', 'name': 'X6', 'value': 17000}])
    assert response.status_code == 200
    data = response.json()
    assert data['spec']['items'][2]['place'][0]['vars']['no']['start'] == 401
    assert next(p for p in data['prims'] if p['sources'][0]['item'] == 'items[3]')['points'][0][0] == 17000
    assert len([p for p in data['prims'] if p['sources'][0]['item'] == 'items[2].items[1]']) == 7


@pytest.mark.parametrize('x, expected', [(3300, 'X2'), (3150, 'X2-150'), (3750, 'X2+450')])
def test_move_preserves_grid_reference(editing, x, expected):
    work, c = editing
    raw = read(c)['spec']
    raw['items'][3]['points'][0][0] = 'X2+300'
    (work/'plan.json').write_text(json.dumps(raw), encoding='utf8')
    data = read(c)
    core = next(p for p in data['prims'] if p['sources'][0]['item'] == 'items[3]')
    response = edit(c, [{'op': 'move_point', 'source': core['sources'][0], 'field': 'points', 'index': 0, 'world': [x, -150]}])
    assert response.status_code == 200
    assert response.json()['spec']['items'][3]['points'][0] == [expected, 'Y1-150']
    # 축 이름과 오프셋을 보존하면서 세계 좌표도 일치한다.
    point = next(p for p in response.json()['prims'] if p['sources'][0]['item'] == 'items[3]')['points'][0]
    assert point == [x, -150]


@pytest.mark.parametrize('rotation,mirror,repeat,nested', [(90,None,0,False),(0,'x',0,False),(23,'y',2,False),(90,'x',2,True)])
def test_move_inverse_transform(editing, rotation, mirror, repeat, nested):
    work, c = editing
    raw = read(c)['spec']
    leaf = {'kind':'line','layer':'WAL','start':[10,20],'end':[30,40]}
    group = {'kind':'group','items':[leaf], 'place':[{'at':[100,200], 'rotation':rotation, 'mirror':mirror, 'repeat':{'count':3,'step':[500,700]}}]}
    raw['items'] = [{'kind':'group','items':[group], 'place':[{'at':[3000,4000], 'rotation':37,'mirror':'y'}]}] if nested else [group]
    (work/'plan.json').write_text(json.dumps(raw),encoding='utf8')
    prim = read(c)['prims'][repeat]
    world = [4321.123456, -1234.654321]
    response = edit(c, [{'op':'move_point','source':prim['sources'][0],'field':'start','world':world}])
    assert response.status_code == 200
    assert response.json()['prims'][repeat]['points'][0] == pytest.approx(world, abs=1e-6)


def test_numeric_coordinates_stay_numeric(editing):
    _, c = editing
    p = next(p for p in read(c)['prims'] if p['sources'][0]['item']=='items[2].items[3]')
    response = edit(c,[{'op':'move_point','source':p['sources'][0],'field':'center','world':[2400,6100]}])
    assert response.status_code == 200
    assert response.json()['spec']['items'][2]['items'][3]['center'] == [2400,6100]


def test_validation_and_conflict_leave_original(editing):
    work, c = editing
    original = (work/'plan.json').read_bytes()
    bad = edit(c,[{'op':'set','target':'items[2].items[3]','field':'radius','value':-1}])
    assert bad.status_code == 422 and bad.json()['violations']
    assert (work/'plan.json').read_bytes() == original
    old = read(c)
    (work/'plan.json').write_bytes(original+b' ')
    conflict = edit(c,[{'op':'set_grid','axis':'X','name':'X2','value':3400}],sha=old.get('sha256','stale'))
    assert conflict.status_code == 409
    assert conflict.json()['spec'] == old['spec']
    assert conflict.json()['sha256'] == hashlib.sha256(original+b' ').hexdigest()
    assert not (work/'review/history').exists()


def test_expand_failure_is_422(editing):
    work,c = editing
    original=(work/'plan.json').read_bytes()
    response=edit(c,[{'op':'set','target':'items[2].items[1]','field':'id','value':'wall'}])
    assert response.status_code==422
    assert (work/'plan.json').read_bytes()==original


def test_undo_steps_back_one_edit_at_a_time_and_history_cap(editing):
    # 되돌리기는 누를 때마다 한 단계씩 이전으로 간다(되돌린 것을 다시 되살리지 않음).
    work,c=editing
    original=(work/'plan.json').read_bytes()
    edit(c,[{'op':'set_grid','axis':'X','name':'X3','value':6700}])
    first_bytes=(work/'plan.json').read_bytes()
    second=edit(c,[{'op':'set_grid','axis':'X','name':'X3','value':6800}]).json()
    undo=c.post('/api/spec/undo',json={'path':'plan.json','sha256':second.get('sha256','')})
    assert undo.status_code==200 and (work/'plan.json').read_bytes()==first_bytes
    again=c.post('/api/spec/undo',json={'path':'plan.json','sha256':undo.json()['sha256']})
    assert again.status_code==200 and (work/'plan.json').read_bytes()==original
    empty=c.post('/api/spec/undo',json={'path':'plan.json','sha256':again.json()['sha256']})
    assert empty.status_code==422 and (work/'plan.json').read_bytes()==original
    assert not list((work/'review/history/plan.json').glob('*.json'))
    for i in range(52):
        assert edit(c,[{'op':'set_grid','axis':'X','name':'X3','value':6800+i}]).status_code==200
    assert len(list((work/'review/history/plan.json').glob('*.json')))==50


def test_edits_log_and_cli(editing,capsys):
    work,c=editing
    assert edit(c,[{'op':'set','target':'items[2].items[1]','field':'points','index':1,'value':[1700,3800]}]).status_code==200
    row=json.loads((work/'review/edits.jsonl').read_text(encoding='utf8').strip())
    assert row['before_sha']!=row['after_sha'] and row['file']=='plan.json'
    assert '10' in row['summary'] and '1600' in row['summary'] and '1700' in row['summary']
    from src.ui.edits import main
    assert main(['--work',str(work),'log','--json'])==0
    assert json.loads(capsys.readouterr().out)==[row]
    assert main(['--work',str(work),'log','--since','2999-01-01T00:00:00+00:00','--json'])==0
    assert json.loads(capsys.readouterr().out)==[]


def test_ops_hashes_and_bound_copy(editing,capsys):
    work,c=editing
    from src.engine.spec import parse_spec,spec_hash
    from tools.ops.ops import main
    raw=read(c)['spec']
    path=raw['op']+'.json'
    (work/'plan.json').rename(work/path)
    (work/'hashes.json').write_text(json.dumps([{'op':raw['op'],'spec_hash':spec_hash(parse_spec(raw)),'bytes':(work/path).stat().st_size}]))
    response=edit(c,[{'op':'set_grid','axis':'X','name':'X3','value':6750}],path=path)
    assert response.status_code==200
    assert main(['verify',str(work)])==0
    capsys.readouterr()
    assert json.loads((work/'hashes.json').read_text())[0]['bytes']==(work/path).stat().st_size
    (work/'bind.json').write_text('{}')
    before=(work/path).read_bytes()
    assert edit(c,[{'op':'set_grid','axis':'X','name':'X3','value':6800}],path=path).status_code==403
    assert c.post('/api/spec/undo',json={'path':path,'sha256':response.json()['sha256']}).status_code==403
    assert (work/path).read_bytes()==before


@pytest.mark.parametrize('path',['../outside.json','..\\outside.json','C:/outside.json','/outside.json'])
def test_outside_path_rejected(editing,path):
    _,c=editing
    assert c.post('/api/spec/edit',json={'path':path,'sha256':'x','ops':[]}).status_code==403


def test_parallel_edits_have_one_winner(editing):
    _,c=editing
    sha=read(c).get('sha256','')
    def save(value):
        return edit(c,[{'op':'set_grid','axis':'X','name':'X3','value':value}],sha=sha).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save,[6700,6800]))==[200,409]


def test_handles_track_original_vertices_and_transforms(editing):
    _,c=editing
    data=read(c)
    bath=next(p for p in data['prims'] if p['sources'][0]['item']=='items[2].items[1]' and p['sources'][0]['groups'][0]['place']==1)
    handles=data.get('handles',{}).get(bath['oid'],[])
    assert len(handles)==3
    assert handles[1]['field']=='points' and handles[1]['index']==1
    assert handles[1]['world']==bath['points'][1]
    assert handles[1]['source']==bath['sources'][0]


@pytest.mark.parametrize('destination',['plan.json','hashes.json','edits.jsonl'])
def test_atomic_failure_rolls_back_all_files(editing,monkeypatch,destination):
    work,c=editing
    from src.engine.spec import parse_spec,spec_hash
    import src.ui.edit as module
    raw=read(c)['spec']
    raw['op']='plan'
    (work/'plan.json').write_text(json.dumps(raw),encoding='utf8')
    (work/'hashes.json').write_text(json.dumps([{'op':'plan','spec_hash':spec_hash(parse_spec(raw))}]))
    original=(work/'plan.json').read_bytes()
    hashes=(work/'hashes.json').read_bytes()
    real_replace=module.os.replace
    failed=False
    def fail_once(src,dest):
        nonlocal failed
        if Path(dest).name==destination and not failed:
            failed=True
            raise OSError('시험 저장 실패')
        return real_replace(src,dest)
    monkeypatch.setattr(module.os,'replace',fail_once)
    response=edit(c,[{'op':'set_grid','axis':'X','name':'X3','value':6800}])
    assert response.status_code==503 and failed
    assert (work/'plan.json').read_bytes()==original
    assert (work/'hashes.json').read_bytes()==hashes
    assert not (work/'review/edits.jsonl').exists()
    assert not [p for p in (work/'review/history').rglob('*.json') if p.is_file()]
    assert not list(work.rglob('*.tmp'))


def test_malformed_move_source_and_operation_keep_file(editing):
    work,c=editing
    before=(work/'plan.json').read_bytes()
    for op in [
        {'op':'move_point','source':{'item':'items[2].items[1]','groups':[]},'field':'points','index':1,'world':[1,2]},
        {'op':'move_point','source':{'item':'items[3]','groups':[]},'field':'points','index':-1,'world':[1,2]},
        {'op':'set','target':'items[99]','field':'start','value':[1,2]},
        {'op':'set_place','group':'items[2]','place':0,'field':'repeat.step','value':[1]},
    ]:
        assert edit(c,[op]).status_code==422
        assert (work/'plan.json').read_bytes()==before


def test_edits_cli_as_module(editing):
    work,c=editing
    import subprocess
    import sys
    assert edit(c,[{'op':'set_grid','axis':'X','name':'X3','value':6800}]).status_code==200
    result=subprocess.run([sys.executable,'-X','utf8','-m','src.ui.edits','--work',str(work),'log','--json'],cwd=ROOT,capture_output=True,text=True,encoding='utf8')
    assert result.returncode==0
    assert json.loads(result.stdout)[0]['file']=='plan.json'


@pytest.mark.parametrize('location',['review','review/edits.jsonl','review/history','hashes.json'])
def test_external_storage_links_rejected(editing,tmp_path,location):
    work,c=editing
    # pytest의 바깥 형제 폴더에 링크를 만들어 작업 폴더 경계를 확인한다.
    outside=work.parent/(work.name+'-outside')
    outside.mkdir()
    destination=work/location
    destination.parent.mkdir(exist_ok=True,parents=True)
    directory=location in ('review','review/history')
    linked=outside if directory else outside/'data.json'
    if not directory: linked.write_text('[]')
    try:
        destination.symlink_to(linked,target_is_directory=directory)
    except OSError:
        pytest.skip('심볼릭 링크 권한 없음')
    before=(work/'plan.json').read_bytes()
    response=edit(c,[{'op':'set_grid','axis':'X','name':'X3','value':6800}])
    assert response.status_code==403
    assert (work/'plan.json').read_bytes()==before
    assert not list(outside.rglob('*.tmp'))


@pytest.mark.parametrize('tag',['축열','SL-80','FL%%P0'])
def test_standard_block_attribute_fields_keep_original_keys(editing,tag):
    work,c=editing
    from src.engine.standards import load_standard
    standard=load_standard()
    block=next(b for b in standard.blocks if any(a.tag==tag for a in b.attributes))
    raw=read(c)['spec']
    raw['items']=[{'kind':'block','name':block.name,'at':[0,0],'attribs':{tag:'before'}}]
    (work/'plan.json').write_text(json.dumps(raw,ensure_ascii=False),encoding='utf8')
    assert not read(c)['violations']
    response=edit(c,[{'op':'set','target':'items[0]','field':'attribs.'+tag,'value':'after'}])
    assert response.status_code==200
    assert response.json()['spec']['items'][0]['attribs'][tag]=='after'
    assert response.json()['prims'][0]['attribs'][tag]=='after'


@pytest.mark.parametrize('value',['NaN','Infinity','-Infinity','1e999'])
def test_nonfinite_expansion_never_commits(editing,value):
    work,c=editing
    original=(work/'plan.json').read_bytes()
    response=edit(c,[{'op':'set','target':'items[0].items[0]','field':'start[0]','value':value}])
    assert response.status_code==422
    assert response.json()['violations']
    assert (work/'plan.json').read_bytes()==original
    assert not (work/'review/edits.jsonl').exists()


def test_dimension_chain_handles_only_its_two_endpoints(editing):
    work,c=editing
    raw=read(c)['spec']
    raw['items']=[{'kind':'dim_chain','id':'chain','style':'DIM330','points':[[i*100,0] for i in range(100)],'direction':'x','line_at':300}]
    (work/'plan.json').write_text(json.dumps(raw),encoding='utf8')
    data=read(c)
    assert len(data['prims'])==99
    handles=data['handles'][data['prims'][40]['oid']]
    assert len(handles)==2
    assert [h['index'] for h in handles]==[40,41]
