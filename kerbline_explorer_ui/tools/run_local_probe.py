#!/usr/bin/env python3
"""Run a real local PoC bank gate and export its component-scoped telemetry.

This uses the existing synthetic ERP adapter, not a live customer ERP or model.
It does not generate model usage or claim AU inference / mTLS / WORM evidence.
"""
import argparse
import json
import os
from pathlib import Path
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials-file',required=True)
    parser.add_argument('--collector',default='http://127.0.0.1:8790/api/telemetry/ingest')
    args=parser.parse_args()
    configuration=json.loads(Path(args.credentials_file).read_text())
    producer=next((p for p in configuration.get('producers',[]) if p.get('subject')=='local-phase-c-poc'),None)
    if not producer:
        raise SystemExit('No local-phase-c-poc producer configured. No telemetry was sent.')
    os.environ['KERBLINE_TELEMETRY_URL']=args.collector
    os.environ['KERBLINE_TELEMETRY_TOKEN']=producer['token']
    root=Path(__file__).resolve().parents[2]
    sys.path.insert(0,str(root));sys.path.insert(0,str(root/'phase_c_poc'))
    from kerbline_telemetry import exporter
    from app.host_rpc_bridge import run_v8_sandbox
    script='const result = await kerbline.change_supplier_bank_details({supplierAbn: "33051775556"}); return {status: result.status};'
    result=run_v8_sandbox(script,invoice_ref='LOCAL-POC-INVOICE')
    delivered=exporter.flush(timeout=12)
    health=exporter.health()
    print(json.dumps({'traceId':result.get('traceId'),'executionOk':result.get('ok'),
                      'scope':'COMPONENT','businessData':'Existing synthetic ERP adapter','modelCalls':'None made by this probe',
                      'delivered':delivered and health['delivered']>0,'exporter':health},indent=2))
    return 0 if result.get('ok') and health['delivered']>0 else 1


if __name__=='__main__':sys.exit(main())
