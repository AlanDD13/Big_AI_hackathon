export type NoticeKind = 'found' | 'classified' | 'going' | 'grabbed' | 'finished' | 'manual';

export type RoverNotice = {
  id: string;
  kind: NoticeKind;
  text: string;
};

export const noticeLabel: Record<NoticeKind, string> = {
  found: 'Object found',
  classified: 'Classified',
  going: 'Going to it',
  grabbed: 'Grabbed',
  finished: 'Finished',
  manual: 'Needs a person',
};

/** Local preview until backend events replace this list. Sector B2 is the demo-grid example, not a live detection. */
export const previewNotices: RoverNotice[] = [
  { id: 'preview-found-bottle', kind: 'found', text: 'Object found.' },
  { id: 'preview-class-bottle', kind: 'classified', text: 'Classified as a water bottle.' },
  { id: 'preview-going', kind: 'going', text: 'Going to it.' },
  { id: 'preview-grabbed', kind: 'grabbed', text: 'Grabbed.' },
  { id: 'preview-finished', kind: 'finished', text: 'Finished.' },
  { id: 'preview-found-chair', kind: 'found', text: 'Object found.' },
  { id: 'preview-class-chair', kind: 'classified', text: 'Classified as a chair.' },
  { id: 'preview-manual', kind: 'manual', text: 'Not grabbable. Grab it in sector B2.' },
];
