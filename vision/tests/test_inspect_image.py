import asyncio
from pathlib import Path

from vision.classification import FixtureClassifier
from vision.inspect_image import inspect_image, load_env_file


def test_env_loader_reads_only_allowed_values_without_executing_shell(tmp_path, monkeypatch):
    for key in ('XAI_API_KEY', 'GROK_VISION_PROVIDER', 'GROK_VISION_MODEL'):
        monkeypatch.delenv(key, raising=False)
    marker = tmp_path / 'executed'
    config = tmp_path / '.env'
    config.write_text('export XAI_API_KEY="local-test-key"\n'
                      'GROK_VISION_PROVIDER=xai\n'
                      'GROK_VISION_MODEL=grok-4.7\n'
                      f'touch {marker}\nUNRELATED_SECRET=ignored\n')
    load_env_file(config)
    import os
    assert os.environ['XAI_API_KEY'] == 'local-test-key'
    assert not marker.exists()
    assert 'UNRELATED_SECRET' not in os.environ


def test_fixture_cannot_fake_verified_pickup_or_live_image_assessment():
    result = asyncio.run(inspect_image(b'invalid-image', FixtureClassifier()))
    assert result['visualPickupAdvisory']['status'] == 'UNAVAILABLE'
    assert result['dispatchAuthorized'] is False
    assert result['pickupVerified'] is False


def test_pickup_check_is_bound_to_crop_instead_of_a_distracting_context_object():
    from vision.classification import ClassificationResult, VisualPickupResult
    calls = []
    class Model:
        provider = 'grok'
        async def classify(self, crop, context):
            calls.append(('classify', crop, context))
            return ClassificationResult(category='TRASH', subject_kind='INANIMATE', carrying='NO',
                                        confidence='HIGH', description='paper', reason='crop shows paper')
        async def assess_pickup(self, crop, context):
            calls.append(('pickup', crop, context))
            return VisualPickupResult(object_form='CRUMPLED', visual_suitability='POSSIBLE', reason='paper shape')
    result = asyncio.run(inspect_image(b'paper-crop', Model(), b'large-neighbouring-can-scene'))
    assert calls == [('classify', b'paper-crop', b'large-neighbouring-can-scene'),
                     ('pickup', b'paper-crop', b'paper-crop')]
    assert not result['dispatchAuthorized']


def test_joint_assessment_uses_one_call_for_identity_and_robot_estimate():
    from vision.classification import ObjectAssessment, ClassificationResult, VisualPickupResult
    class Model:
        provider = 'grok'
        calls = 0
        async def assess_object(self, crop, context):
            self.calls += 1
            return ObjectAssessment(
                classification=ClassificationResult(category='TRASH', subject_kind='INANIMATE',
                    carrying='NO', confidence='HIGH', description='crumpled paper', reason='target crop'),
                pickup=VisualPickupResult(object_form='CRUMPLED', visual_suitability='POSSIBLE',
                    reason='raised lightweight shape', robot_grasp_estimate='LIKELY',
                    assumptions=['loose lightweight paper'], required_checks=['confirm size and reach']))
    model = Model()
    result = asyncio.run(inspect_image(b'paper', model, b'context', prefer_joint=True))
    assert model.calls == 1
    assert result['classification']['result']['description'] == 'crumpled paper'
    assert result['visualPickupAdvisory']['result']['robot_grasp_estimate'] == 'LIKELY'
    assert not result['pickupVerified']
