import type { Job } from '../lib/api';
import { jobCaptureModes } from '../lib/capture-modes';
import { ButtonLink } from './Button';

/** Capture entry points as secondary buttons with short labels (no wrapping at 390). */
export function captureLinks(job: Job) {
  const flags = jobCaptureModes(job);
  const id = encodeURIComponent(job.id);
  const links = [];
  if (flags.tour) links.push(<ButtonLink key="tour" to={`/capture?jobId=${id}&kind=splat`}>3D tour</ButtonLink>);
  if (flags.floorplan) links.push(<ButtonLink key="fp" to={`/capture?jobId=${id}&kind=floorplan`}>Floor plan</ButtonLink>);
  if (flags.visit) links.push(<ButtonLink key="visit" to={`/capture/visit?jobId=${id}`}>Virtual visit</ButtonLink>);
  return links;
}
