import { perceptionFacts } from './perception';
import type { BackendObject } from './backend';

const label = (value: string | null) => value ? value.replaceAll('_', ' ') : '—';
export default function PerceptionDetails({ object }: { object: BackendObject }) {
  const facts = perceptionFacts(object);
  return <>
    <h4>Classification</h4><dl className="backend-facts">
      <div><dt>Status</dt><dd>{facts.classificationStatus ?? 'Not supplied'}</dd></div>
      <div><dt>Category</dt><dd>{label(facts.category)}</dd></div>
      <div><dt>Description</dt><dd>{facts.description ?? '—'}</dd></div>
      <div><dt>Detector score</dt><dd>{facts.detectorScore === null ? '—' : `${Math.round(facts.detectorScore * 100)}%`}</dd></div>
      <div><dt>Classification confidence</dt><dd>{facts.confidence ?? '—'}</dd></div>
      <div><dt>Provider</dt><dd>{facts.provider ?? '—'}</dd></div>
      <div><dt>Reason</dt><dd>{facts.reason ?? '—'}</dd></div>
    </dl>
    <h4 className="advisory-heading">Visual pickup <span className="badge gold">Advisory only</span></h4>
    {facts.advisory ? <div className="visual-advisory"><dl className="backend-facts">
      <div><dt>Object form</dt><dd>{label(facts.advisory.form)}</dd></div>
      <div><dt>Visual suitability</dt><dd>{facts.advisory.suitability}</dd></div>
      <div><dt>Reason</dt><dd>{facts.advisory.reason}</dd></div>
      <div><dt>Source frame</dt><dd>{facts.advisory.sourceFrameId ?? '—'}</dd></div>
    </dl><p>Visual appearance only. Size, reach, grip and payload still require physical checks.</p></div> : <div className="evidence-empty">{facts.visualStatus === 'PENDING' ? 'Visual assessment pending.' : 'No visual assessment available.'}</div>}
    <p className="pickup-unverified">Pickup not physically verified. This is not a successful grab or dispatch permission.</p>
  </>;
}
