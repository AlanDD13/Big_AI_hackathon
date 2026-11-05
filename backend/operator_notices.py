"""Deterministic operator messages derived from accepted backend facts."""
import hashlib
import re


STEP_TEXT = {
    'FORWARD_30': ('going', 'I moved 30 centimetres forward.'),
    'TURN_RIGHT_90': ('going', 'I turned 90 degrees to the right.'),
    'FORWARD_154': ('going', 'I completed the 154 centimetre forward leg.'),
    'PICKUP': ('grabbed', 'I closed the gripper and lifted the arm. Holding is not independently verified.'),
    'RETURNED': ('going', 'I returned to my starting position.'),
    'LOWERED': ('going', 'I lowered the arm for release.'),
    'RELEASED': ('going', 'I opened the gripper at the starting position.'),
}


def notices_for(event_type, body):
    object_id = body.get('objectId')
    if not isinstance(object_id, str):
        return []
    revision = body.get('revision', 0)
    entries = []
    if event_type.startswith('object.') and body.get('status') not in ('LOST', 'EXCLUDED'):
        entries.append(('found', 'Object found.', 'found'))
        classification = body.get('classification')
        if body.get('classificationStatus') == 'COMPLETED' and isinstance(classification, dict):
            description = str(classification.get('description') or body.get('detectorLabel') or 'an object')[:160]
            description = re.sub(r'[<>\[\]]', '', description)
            entries.append(('classified', f'Classified as {description.rstrip(".")}.', f'classified:{revision}'))
    kind = body.get('kind')
    source = body.get('object') or {}
    if kind == 'pickup_unavailable':
        reason = str(body.get('reason', 'UNKNOWN'))
        text = {'GRABBABILITY_LOW_OR_UNKNOWN': 'The grasp estimate is low or unknown. A person needs to check this object.',
                'UNSUPPORTED_PICKUP_PROFILE': 'This object needs a person. Its pickup profile is not supported.',
                'RECOVERY_NOT_ARMED': 'The object was assessed. Automatic recovery is not armed.',
                'CLASSIFICATION_EXCLUDED': 'This classification is excluded from automatic pickup.'}.get(
                    reason, 'Automatic pickup is unavailable. Please check the object and recovery status.')
        entries.append(('manual', text, f'manual:{revision}'))
    elif kind == 'recovery_started':
        entries.append(('going', 'I am starting the recovery route.', f'started:{revision}'))
    elif kind == 'recovery_step' and source.get('recoveryStep') in STEP_TEXT:
        step = source['recoveryStep']
        notice_kind, text = STEP_TEXT[step]
        entries.append((notice_kind, text, f'step:{step}:{revision}'))
    elif kind == 'recovery_completed':
        entries.append(('finished', 'I completed the return and release sequence. Please confirm the object is on the floor.', f'finished:{revision}'))
    elif kind == 'recovery_failed':
        entries.append(('manual', 'Recovery needs an operator check. I will not automatically repeat the movement.', f'failed:{revision}'))
    advisory = (source if kind else body).get('visualPickupAdvisory') or {}
    assessment = source if kind else body
    classification = assessment.get('classification') or {}
    score = advisory.get('grabbability_score')
    if not kind and isinstance(score, (float, int)) and not isinstance(score, bool) and 0 <= score <= 1:
        entries = [(k, text + f' Grabbability estimate: {round(score * 100)} percent.' if k == 'classified' else text, key)
                   for k, text, key in entries]
    return [dict(id=hashlib.sha256(f'{object_id}:{key}'.encode()).hexdigest()[:32],
                 kind=notice_kind, text=text, objectId=object_id,
                 grabbabilityScore=advisory.get('grabbability_score'),
                 classificationModel=assessment.get('classificationModel'),
                 classificationConfidence=classification.get('confidence'))
            for notice_kind, text, key in entries]
