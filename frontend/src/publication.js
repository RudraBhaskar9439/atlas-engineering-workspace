export function publicationReadiness(run, destination) {
 const validation=run?.validation||{}, ci=run?.ci||{};
 const currentPatch=!!run?.patch_hash&&validation.patch_hash===run.patch_hash;
 const checksPassed=currentPatch&&validation.status==='passed';
 const unverified=!checksPassed&&currentPatch&&validation.kind==='github_ci'
  &&['failed','pending','not_started'].includes(validation.status)
  &&!!validation.head_sha&&validation.head_sha===ci.head_sha
  &&!!destination&&destination.trim().toLowerCase()===validation.destination;
 return {checksPassed,unverified:!!unverified,canPublish:checksPassed||!!unverified};
}
