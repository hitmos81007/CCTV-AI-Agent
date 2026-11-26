import React,{useEffect,useState} from "react";
import {Link,useParams} from "react-router-dom";
import {useMutation,useQuery,useQueryClient} from "@tanstack/react-query";
import {MapContainer,TileLayer,CircleMarker,Popup} from "react-leaflet";
import {request,evidenceImage} from "../api";
import type {Case,Camera,Progress,Recording,Sighting} from "../types";
import {ErrorBox,post} from "../components/Common";

function Frame({path}:{path:string}){
  const [url,setUrl]=useState(""),[error,setError]=useState("");
  useEffect(()=>{let live=true,object="";evidenceImage(path).then(blob=>{object=URL.createObjectURL(blob);if(live)setUrl(object)}).catch(e=>{if(live)setError(e.message)});return()=>{live=false;if(object)URL.revokeObjectURL(object)}},[path]);
  return url?<img src={url} alt="Evidence frame"/>:<p>{error||"Loading image…"}</p>;
}

type Review={reviewer:string;decision:string;note:string;created_at:string};
function ReviewHistory({id}:{id:string}){
  const history=useQuery({queryKey:["review-history",id],queryFn:()=>request<Review[]>("/api/v1/sightings/"+encodeURIComponent(id)+"/reviews")});
  return <div>{history.isLoading&&<p>Loading history…</p>}<ErrorBox error={history.error}/>{history.data?.length===0&&<p>No review history.</p>}{history.data?.map((r,i)=><p key={i}>{r.created_at} · {r.reviewer} · {r.decision} {r.note&&"· "+r.note}</p>)}</div>;
}

function EvidenceCard({s,reviewer,note}:{s:Sighting;reviewer:string;note:string}){
  const cache=useQueryClient(),[showHistory,setShowHistory]=useState(false),[message,setMessage]=useState("");
  const review=useMutation({mutationFn:(decision:string)=>request("/api/v1/sightings/"+encodeURIComponent(s.id)+"/review",post({reviewer,decision,note})),onSuccess:async()=>{setMessage("Review saved.");await Promise.all([cache.invalidateQueries({queryKey:["sightings"]}),cache.invalidateQueries({queryKey:["review-history",s.id]})])}});
  function decide(decision:string){if(window.confirm("Save this review decision?")){setMessage("");review.mutate(decision)}}
  return <article>{s.frame_url?<Frame path={s.frame_url}/>:<p>No saved frame for this candidate.</p>}<strong>{s.vehicle_type} · {s.jurisdiction}</strong><p>{s.camera_id} · {s.occurred_at}</p><p>Detector confidence {Number(s.detector_confidence).toFixed(2)} · {s.review_status}</p><p>Recording: {s.video_source_id||"Unavailable"}</p><button disabled={!reviewer||review.isPending} onClick={()=>decide("confirmed_candidate")}>Accept</button>{" "}<button disabled={!reviewer||review.isPending} onClick={()=>decide("not_relevant")}>Reject</button>{" "}<button onClick={()=>setShowHistory(!showHistory)}>{showHistory?"Hide history":"Review history"}</button>{review.isPending&&<p role="status">Saving review…</p>}{message&&<p role="status">{message}</p>}<ErrorBox error={review.error}/>{showHistory&&<ReviewHistory id={s.id}/>}</article>;
}

function Playback({videoId}:{videoId:string}){
  const [url,setUrl]=useState(""),[error,setError]=useState("");
  useEffect(()=>()=>{if(url)URL.revokeObjectURL(url)},[url]);
  async function open(){try{const blob=await evidenceImage("/api/v1/videos/"+encodeURIComponent(videoId)+"/file");setUrl(URL.createObjectURL(blob))}catch(e){setError(e instanceof Error?e.message:"Video unavailable")}}
  return <div>{url?<video controls src={url} style={{maxWidth:"100%"}}/>:<button onClick={open}>Load video</button>}<ErrorBox error={error}/></div>;
}

export function Workspace(){
  const {id=""}=useParams(),key=encodeURIComponent(id),cache=useQueryClient();
  const [reviewer,setReviewer]=useState(""),[note,setNote]=useState(""),[filter,setFilter]=useState(""),[page,setPage]=useState(0);
  const item=useQuery({queryKey:["case",id],queryFn:()=>request<Case>("/api/v1/cases/"+key)});
  const cameras=useQuery({queryKey:["cameras"],queryFn:()=>request<Camera[]>("/api/v1/cameras")});
  const records=useQuery({queryKey:["recordings",id],queryFn:()=>request<{items:Recording[]}>("/api/v1/recordings?case_id="+key)});
  const sightings=useQuery({queryKey:["sightings",id],queryFn:()=>request<Sighting[]>("/api/v1/sightings?case_id="+key)});
  const progress=useQuery({queryKey:["progress",id],queryFn:()=>request<Progress>("/api/v1/cases/"+key+"/progress"),refetchInterval:10000});
  const plan=useMutation({mutationFn:()=>request("/api/v1/cases/"+key+"/plan",post({})),onSuccess:()=>cache.invalidateQueries({queryKey:["progress",id]})});
  const located=(cameras.data||[]).filter(c=>c.latitude!==null&&c.longitude!==null&&Object.hasOwn(item.data?.neighbors||{},c.jurisdiction));
  const filtered=(sightings.data||[]).filter(s=>[s.camera_id,s.jurisdiction,s.vehicle_type,s.review_status].some(v=>v.toLowerCase().includes(filter.toLowerCase())));
  return <><Link to="/">All investigations</Link><h1>{id}</h1>{item.isLoading&&<p>Loading case…</p>}<ErrorBox error={item.error}/><p>{item.data?.description}</p>
    <nav className="tabs"><a href="#activity">Activity</a><a href="#map">Map</a><a href="#recordings">Recordings</a><a href="#evidence">Evidence</a></nav>
    <section id="activity"><h2>Agent activity</h2><button onClick={()=>plan.mutate()} disabled={plan.isPending}>{plan.isPending?"Planning…":"Plan investigation"}</button><ErrorBox error={plan.error}/><ErrorBox error={progress.error}/>{progress.isLoading&&<p>Loading activity…</p>}{progress.data?.jobs.length===0&&<p>No jobs yet.</p>}{progress.data?.jobs.map(j=><div className="row" key={j.id}><strong>{j.jurisdiction}</strong><span>{j.worker_id}</span><span>{j.status}</span></div>)}</section>
    <section><h2>Clues</h2><pre>{item.data?.clues?JSON.stringify(item.data.clues,null,2):"No parsed clue stored."}</pre></section>
    <section id="map"><h2>Jurisdiction map</h2><div className="chips">{Object.entries(item.data?.neighbors||{}).map(([z,n])=><div key={z}><strong>{z}</strong><small>Neighbors: {n.join(", ")||"none"}</small></div>)}</div>{located.length?<MapContainer center={[located[0].latitude!,located[0].longitude!]} zoom={11} style={{height:350,marginTop:20}}><TileLayer attribution="© OpenStreetMap contributors" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"/>{located.map(c=><CircleMarker key={c.camera_id} center={[c.latitude!,c.longitude!]}><Popup>{c.name} · {c.jurisdiction}</Popup></CircleMarker>)}</MapContainer>:<p>No camera coordinates available for these jurisdictions.</p>}<ErrorBox error={cameras.error}/></section>
    <section id="recordings"><h2>Recordings</h2><ErrorBox error={records.error}/>{records.isLoading&&<p>Loading recordings…</p>}{records.data?.items.length===0&&<p>No recordings.</p>}{records.data?.items.map(r=><div className="row" key={r.id}><strong>{r.camera_id}</strong><span>{r.jurisdiction} · {r.source_type}</span><span>{r.status} · {r.recorded_at||"time unavailable"}{r.video_source_id&&<Playback videoId={r.video_source_id}/>}</span></div>)}</section>
    <section id="evidence"><h2>Evidence and review</h2><label>Filter candidates<input value={filter} onChange={e=>{setFilter(e.target.value);setPage(0)}} placeholder="Camera, jurisdiction, class or status"/></label><label>Reviewer name<input value={reviewer} onChange={e=>setReviewer(e.target.value)}/></label><label>Review note<input value={note} onChange={e=>setNote(e.target.value)}/></label><ErrorBox error={sightings.error}/>{sightings.isLoading&&<p>Loading evidence…</p>}{sightings.data&&filtered.length===0&&<p>No candidates found.</p>}<div className="cards">{filtered.slice(page*12,page*12+12).map(s=><EvidenceCard key={s.id} s={s} reviewer={reviewer} note={note}/>)}</div>{filtered.length>12&&<div className="pagination"><button disabled={page===0} onClick={()=>setPage(page-1)}>Previous</button><span>Page {page+1} of {Math.ceil(filtered.length/12)}</span><button disabled={(page+1)*12>=filtered.length} onClick={()=>setPage(page+1)}>Next</button></div>}</section>
  </>;
}
