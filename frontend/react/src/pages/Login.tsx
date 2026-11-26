import React, {useState} from "react";
import {request,setCredentials} from "../api";
import {ErrorBox} from "../components/Common";
export function Login({done}:{done:()=>void}){const [user,U]=useState(""),[password,P]=useState(""),[error,E]=useState("");
async function submit(e:React.FormEvent){e.preventDefault();setCredentials(user,password);try{await Promise.all([request("/api/v1/cases"),request("/api/v1/cameras")]);P("");done()}catch(x){E(x instanceof Error?x.message:"Login failed")}}
return <div className="login"><form onSubmit={submit}><h1>Investigation Network</h1><label>Username<input value={user} onChange={e=>U(e.target.value)} required/></label><label>Password<input type="password" value={password} onChange={e=>P(e.target.value)} required/></label><button>Sign in</button><ErrorBox error={error}/></form></div>}
