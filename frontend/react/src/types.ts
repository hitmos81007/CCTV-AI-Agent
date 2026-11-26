export type Case = {case_id:string; description:string; initial_zone:string; neighbors:Record<string,string[]>; clues:unknown};
export type Camera = {camera_id:string; name:string; jurisdiction:string; latitude:number|null; longitude:number|null};
export type Recording = {id:string; camera_id:string; jurisdiction:string; source_type:string; status:string; recorded_at:string|null; video_source_id:string|null};
export type Sighting = {id:string; camera_id:string; jurisdiction:string; occurred_at:string; vehicle_type:string; detector_confidence:number; review_status:string; frame_url:string|null; video_source_id:string};
export type Progress = {status:string; sighting_count:number; jobs:{id:string; jurisdiction:string; status:string; worker_id:string}[]};
