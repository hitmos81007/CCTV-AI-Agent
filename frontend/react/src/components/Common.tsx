import React from "react";
const post=(data:unknown):RequestInit=>({method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});
export function ErrorBox({error}:{error:unknown}){return error?<p className="error" role="alert">{error instanceof Error?error.message:String(error)}</p>:null}
export {post};
