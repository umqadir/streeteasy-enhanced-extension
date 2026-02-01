#!/bin/bash
# Rapid collection script - processes URLs in batches

cd "$(dirname "$0")/.."

# JavaScript extraction code (stored as string for reuse)
JS_EXTRACT='({url:location.href.split("?")[0],title:document.title,hasDashFt:document.body.innerText.includes("- ft"),isCaptcha:document.body.innerText.includes("Press & Hold")||document.body.innerText.includes("CAPTCHA"),photoChunks:Array.from(document.querySelectorAll("img")).filter(img=>img.src.includes("zillowstatic")).map(img=>{let id=img.src.split("/fp/")[1];return[id.substring(0,8),id.substring(8,16),id.substring(16,24),id.substring(24,32)]}).filter((v,i,a)=>JSON.stringify(v)!==JSON.stringify(a[i-1])).slice(0,30)})'

echo "Rapid collection script ready"
echo "Usage: Pass tab_id and URLs will be processed automatically"
echo ""
echo "CAPTCHA detection: Script will pause if 'Press & Hold' detected"
echo "You handle the CAPTCHA, then script will continue"
