"""OBSERVE_ONLY causal/look-ahead diagnostics.
No signal routing, thresholds or Telegram behaviour is changed.
"""
from __future__ import annotations
import ast, hashlib, json, math
from itertools import combinations

SUSPICIOUS_CALLS = {"shift": "negative shift", "pct_change": "negative periods"}


def static_lookahead_findings(source: str):
    findings=[]
    try: tree=ast.parse(source)
    except SyntaxError as e: return [{"kind":"SYNTAX_ERROR","line":e.lineno or 0}]
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            name=node.func.attr
            if name=="shift" and node.args:
                arg=node.args[0]
                negative=(isinstance(arg,ast.Constant) and isinstance(arg.value,(int,float)) and arg.value<0) or (isinstance(arg,ast.UnaryOp) and isinstance(arg.op,ast.USub) and isinstance(arg.operand,ast.Constant) and isinstance(arg.operand.value,(int,float)) and arg.operand.value>0)
                if negative: findings.append({"kind":"NEGATIVE_SHIFT","line":node.lineno})
            if name=="rolling":
                for kw in node.keywords:
                    if kw.arg=="center" and isinstance(kw.value,ast.Constant) and kw.value.value is True:
                        findings.append({"kind":"CENTERED_ROLLING","line":node.lineno})
    return findings


def _canon(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",",":"))


def causal_truncation_audit(series, evaluator, min_prefix=20, checkpoints=8):
    """Recompute a decision on prefixes and compare with the same timestamp in full-data output.

    evaluator(data) must return a sequence aligned to data. Any change at timestamp t after
    appending future bars is a causal violation candidate.
    """
    n=len(series)
    if n < min_prefix+2: return {"status":"INSUFFICIENT_DATA","n":n,"violations":[]}
    full=list(evaluator(series))
    if len(full)!=n: return {"status":"INVALID_EVALUATOR","reason":"output_not_aligned","violations":[]}
    start=max(min_prefix,1); step=max(1,(n-start-1)//max(1,checkpoints))
    points=list(range(start,n-1,step))[:checkpoints]
    violations=[]
    for end in points:
        prefix=series[:end+1]; out=list(evaluator(prefix))
        if len(out)!=len(prefix):
            violations.append({"index":end,"reason":"output_not_aligned"}); continue
        if _canon(out[-1]) != _canon(full[end]):
            violations.append({"index":end,"reason":"future_data_changed_past_output"})
    return {"status":"OK","checked":len(points),"violations":violations,"causal":not violations}


def execution_lag_audit(decisions, fills, minimum_lag_bars=1):
    """Pure index-level audit: a fill may not occur on a bar before required execution lag."""
    bad=[]
    for i,(d,f) in enumerate(zip(decisions,fills)):
        if d is None or f is None: continue
        try: lag=int(f)-int(d)
        except (TypeError,ValueError): continue
        if lag < minimum_lag_bars: bad.append({"index":i,"decision_bar":d,"fill_bar":f,"lag":lag})
    return {"status":"OK","minimum_lag_bars":minimum_lag_bars,"violations":bad,"valid":not bad}
