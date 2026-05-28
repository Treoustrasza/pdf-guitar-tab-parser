/**
 * patch_min2.js
 * alphaTab.min.js に noteStringLookup 再構築パッチを適用する。
 *
 * 問題：MusicXML パーサーは note.string を <technical><string> タグで設定するが、
 * これは beat.addNote() の後に実行される。そのため noteStringLookup は
 * note.string=-1 の状態で構築されており、findHammerPullDestination が
 * 目標音符を見つけられない。
 *
 * 修正：Voice.finish() の beat.finish() ループの前に、全 beat の
 * noteStringLookup を再構築するループを挿入する。
 */
const fs = require('fs');
let s = fs.readFileSync('node_modules/@coderline/alphatab/dist/alphaTab.min.js', 'utf8');

// Voice.finish() 内の beat.finish() ループ開始直前を探す
// パターン: shortestDuration=w.DoubleWhole;for(let s=0;s<this.beats.length;s++){const r=this.beats[s];if(r.index=s,r.finish(t,e)
const OLD = 'this.shortestDuration=w.DoubleWhole;for(let s=0;s<this.beats.length;s++){const r=this.beats[s];if(r.index=s,r.finish(t,e)';

if (!s.includes(OLD)) {
    console.error('Pattern NOT found! Cannot patch.');
    console.error('The file may have already been patched or the pattern has changed.');
    process.exit(1);
}

// noteStringLookup 再構築ループを挿入
const PATCH = 'for(let _pi=0;_pi<this.beats.length;_pi++){const _pb=this.beats[_pi];_pb.noteStringLookup.clear();for(const _pn of _pb.notes){if(_pn.isStringed)_pb.noteStringLookup.set(_pn.string,_pn);}}';
const NEW = 'this.shortestDuration=w.DoubleWhole;' + PATCH + 'for(let s=0;s<this.beats.length;s++){const r=this.beats[s];if(r.index=s,r.finish(t,e)';

s = s.replace(OLD, NEW);
fs.writeFileSync('node_modules/@coderline/alphatab/dist/alphaTab.min.js', s, 'utf8');
console.log('Patched alphaTab.min.js successfully (noteStringLookup rebuild).');
