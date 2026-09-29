"""Screenplay-grounded direction, reference coverage and casting. No model calls.

Codex writes the creative analysis. This module checks its evidence and actual
workspace bindings; it never infers character traits from filenames or declares
that a structurally complete reference pack guarantees generated continuity.
"""
import copy
import hashlib
import json
from pathlib import Path

from creator import script_document
from workflow import WorkflowError, load, write, timestamp


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def shot_digest(shot):
    return digest({key: shot.get(key) for key in ('action', 'camera', 'shotType', 'videoPrompt', 'dialogue', 'audioPlan', 'sourceBlockIds')
                   if key != 'audioPlan'} | {'lines': shot.get('audioPlan', {}).get('lines', [])})


def scaffold(project):
    blocks = script_document(project)['blocks']
    return {'schemaVersion': 1, 'revision': 0, 'projectId': project['id'],
        'scriptHash': digest(project.get('script', '')), 'analysisStatus': 'draft',
        'characters': [], 'spaces': [], 'shots': [
            {'shotId': s['id'], 'sourceHash': shot_digest(s),
             'evidenceBlockIds': [b['id'] for b in blocks if s['id'] in b['shotIds']],
             'requirements': [], 'continuityIn': {}, 'continuityOut': {},
             'continuityLinks': [], 'direction': '', 'analysisStatus': 'draft'} for s in project.get('shots', [])]}


def validate_plan(plan, project):
    def fail(message):
        raise WorkflowError(message, 'INVALID_DIRECTION', 400)
    if not isinstance(plan, dict) or plan.get('schemaVersion') != 1 or plan.get('projectId') != project['id']:
        fail('导演分析必须属于当前项目且 schemaVersion=1。')
    if plan.get('scriptHash') != digest(project.get('script', '')):
        fail('剧本已改变，请重新分析受影响段落，不能套用旧分析。')
    blocks = {b['id'] for b in script_document(project)['blocks']}
    shots = {s['id']: s for s in project.get('shots', [])}
    for group, key in [('characters', 'id'), ('spaces', 'id'), ('shots', 'shotId')]:
        rows = plan.get(group)
        if not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get(key), str) or not r[key] for r in rows):
            fail(f'{group} 必须为含稳定 ID 的列表。')
        ids = [r[key] for r in rows]
        if len(ids) != len(set(ids)):
            fail(f'{group} 存在重复 ID。')
        for row in rows:
            if not isinstance(row.get('evidenceBlockIds', []), list) or set(row.get('evidenceBlockIds', [])) - blocks:
                fail(f'{row[key]} 引用了不存在的剧本段落。')
    for row in plan['shots']:
        sid = row['shotId']
        if sid not in shots:
            fail(f'分析镜头不存在：{sid}')
        if not isinstance(row.get('requirements'), list):
            fail(f'{sid} 缺少 requirements 列表。')
        required_ids = []
        for req in row['requirements']:
            if not isinstance(req, dict) or not req.get('id') or not req.get('label'):
                fail(f'{sid} 参考要求缺少 id/label。')
            required_ids.append(req['id'])
            if req.get('mediaType') not in {'image', 'text', 'audio', 'video'} or req.get('usage') not in {'model_reference', 'context', 'voice_identity', 'post_mix'}:
                fail(f'{sid} 参考要求的媒体/用途无效。')
            if not isinstance(req.get('assetIds'), list) or any(not isinstance(a, str) for a in req['assetIds']):
                fail(f'{sid} assetIds 必须为列表。')
        if len(required_ids) != len(set(required_ids)):
            fail(f'{sid} 要求 ID 重复。')
        for link in row.get('continuityLinks', []):
            if link.get('fromShotId') not in shots or not isinstance(link.get('keys'), list):
                fail(f'{sid} 连续性来源无效。')
        if row.get('reuseFromShotId') and (row['reuseFromShotId'] not in shots or row['reuseFromShotId'] == sid):
            fail(f'{sid} 复用来源无效。')
    reuse = {r['shotId']: r.get('reuseFromShotId') for r in plan['shots']}
    for sid in reuse:
        seen, current = set(), sid
        while current:
            if current in seen:
                fail('复用来源存在循环。')
            seen.add(current)
            current = reuse.get(current)
    return plan


def save_plan(path, plan, project):
    validate_plan(plan, project)
    old = load(path, {'revision': 0})
    if plan.get('revision') != old['revision']:
        raise WorkflowError('导演分析已有新版本，请重读合并。', 'REVISION_CONFLICT', 409)
    result = copy.deepcopy(plan)
    result.update(revision=old['revision'] + 1, updatedAt=timestamp())
    if old['revision']:
        write(Path(path).parent / 'snapshots' / f'direction-r{old["revision"]}.json', old)
    write(path, result)
    return result


def effective_bindings(shot, graph):
    node = next((n for n in graph.get('nodes', []) if n.get('shotId') == shot['id'] and n['type'] in {'video', 'reuse', 'edit'}), None)
    bindings = list((node or {}).get('data', {}).get('bindings', shot.get('bindings', [])))
    # Text specs can be connected upstream of the prompt as well as directly.
    nodes = {n['id']: n for n in graph.get('nodes', [])}
    visited = set()
    def visit(nid):
        if nid in visited:
            return
        visited.add(nid)
        for edge in graph.get('edges', []):
            if edge['target'] != nid:
                continue
            source = nodes.get(edge['source'], {})
            if source.get('assetId'):
                if not any(b['assetId'] == source['assetId'] for b in bindings):
                    bindings.append({'assetId': source['assetId'], 'enabled': True,
                        'usage': edge.get('usage') or ('context' if edge.get('targetPort') == 'context' else 'model_reference')})
            elif source.get('type') == 'prompt':
                visit(source['id'])
    if node:
        visit(node['id'])
    return bindings


def coverage_report(plan, project, graph, asset_document):
    assets = {a['id']: a for a in asset_document.get('assets', [])}
    rows = {r['shotId']: r for r in (plan or {}).get('shots', [])}
    stale_script = not plan or plan.get('scriptHash') != digest(project.get('script', ''))
    shots, gaps = [], {}
    for shot in project.get('shots', []):
        row = rows.get(shot['id'], {})
        errors, warnings, coverage = [], [], []
        if stale_script:
            errors.append('剧本尚未分析或原文已改变')
        if not row or row.get('analysisStatus') != 'prepared' or not row.get('evidenceBlockIds'):
            errors.append('缺少有原文依据的逐镜分析')
        if row and row.get('sourceHash') != shot_digest(shot):
            errors.append('画面/对白已改变，需要重新检查本镜分析')
        bindings = effective_bindings(shot, graph)
        linked = {b['assetId']: b for b in bindings if b.get('enabled', True)}
        for requirement in row.get('requirements', []):
            found = []
            for aid in requirement['assetIds']:
                asset, binding = assets.get(aid, {}), linked.get(aid, {})
                pinned = requirement.get('assetHashes', {}).get(aid)
                if (binding.get('usage') == requirement['usage'] and asset.get('mediaType') == requirement['mediaType']
                        and (not pinned or pinned == asset.get('sha256'))
                        and asset.get('reviewStatus') == 'ready' and Path(asset.get('path') or '__missing__').is_file()):
                    found.append(aid)
            ok = bool(found)
            coverage.append({**requirement, 'covered': ok, 'resolvedAssetIds': found})
            if not ok:
                errors.append('缺少可用并已连接的参考：' + requirement['label'])
                key = requirement.get('gapKey') or requirement['id']
                entry = gaps.setdefault(key, {'id': key, 'label': requirement['label'], 'shotIds': [], 'assetIds': requirement['assetIds']})
                entry['shotIds'].append(shot['id'])
        reuse = row.get('reuseFromShotId')
        if not coverage and not reuse:
            errors.append('未声明画面所需参考；不能以零项检查判定通过')
        for link in row.get('continuityLinks', []):
            previous = rows.get(link['fromShotId'], {}).get('continuityOut', {})
            for key in link['keys']:
                if key not in previous or key not in row.get('continuityIn', {}) or previous[key] != row['continuityIn'][key]:
                    errors.append(f'连续性不符：{link["fromShotId"]} → {shot["id"]} · {key}')
        if reuse:
            warnings.append('复用镜头：等待来源镜头 ' + reuse + ' 的批准媒体，不新生成')
        warnings.extend(row.get('warnings', []))
        shots.append({'shotId': shot['id'], 'title': shot['title'], 'referenceReady': not errors,
            'coverage': coverage, 'errors': errors, 'warnings': warnings, 'direction': row.get('direction', ''),
            'continuityIn': row.get('continuityIn', {}), 'continuityOut': row.get('continuityOut', {}),
            'voiceCues': row.get('voiceCues', []), 'reuseFromShotId': reuse,
            'frameReview': row.get('frameReview', 'pending')})
    by_id = {s['shotId']: s for s in shots}
    for row in shots:
        seen = {row['shotId']}
        source = row.get('reuseFromShotId')
        while source:
            if source in seen or source not in by_id:
                row['errors'].append('复用链无效或循环')
                break
            seen.add(source)
            parent = by_id[source]
            if parent['errors']:
                row['errors'].append('复用来源参考尚未准备好：' + source)
                break
            source = parent.get('reuseFromShotId')
        row['referenceReady'] = not row['errors']
    return {'revision': (plan or {}).get('revision', 0), 'projectId': project['id'],
        'summary': {'shots': len(shots), 'referenceReady': sum(s['referenceReady'] for s in shots),
                    'needsWork': sum(not s['referenceReady'] for s in shots), 'uniqueGaps': len(gaps),
                    'frameReviewPending': sum(s['frameReview'] != 'approved' for s in shots)},
        'shots': shots, 'gaps': sorted(gaps.values(), key=lambda g: -len(g['shotIds'])),
        'meaning': 'referenceReady 检查分析依据、文件、用途、连接及声明的连续性。不是成片一致性保证；首帧/动作/声音仍须审阅。'}


def audition_spec(plan, voice_id, case_id, mode='design', asset_id=None):
    if mode not in {'design', 'speech'}:
        raise WorkflowError('试听类型无效。', 'INVALID_AUDITION_MODE', 400)
    character = next((c for c in plan.get('characters', []) if c['id'] == voice_id), None)
    if not character:
        raise WorkflowError('声音角色不存在。', 'VOICE_NOT_FOUND', 404)
    case = next((c for c in character.get('auditionCases', []) if c['id'] == case_id), None)
    if not case:
        raise WorkflowError('试听情境不存在。', 'CASE_NOT_FOUND', 404)
    if mode == 'design':
        return {'name': character['name'] + ' · 导演版选音', 'characterId': character.get('characterId'),
            'voiceId': voice_id, 'description': character['voiceDesign'], 'previewText': case['text']}
    if not asset_id:
        raise WorkflowError('逐句试演需要明确音色资产。', 'VOICE_REQUIRED', 400)
    # Direction stays out of spoken text. This adapter has no freeform acting
    # instruction field; do not make a character read the director's notes.
    return {'name': character['name'] + ' · ' + case['id'] + ' · 表演试验',
        'characterId': character.get('characterId'), 'voiceAssetId': asset_id,
        'text': case['text'], 'modelId': 'speech-2.8-hd', 'params': case.get('params', {}),
        'performance': case.get('performance', []), 'auditionOnly': True}
