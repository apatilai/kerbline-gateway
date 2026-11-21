"""Measured synthetic payload token comparison, not a net cost/savings claim."""
import json
from pathlib import Path
import subprocess
import urllib.request

STATE = Path(__file__).resolve().parents[2] / 'tmp' / 'system-design'
URL = 'https://aiplatform.googleapis.com/v1/projects/project-4f0a7dac-0503-4e87-abe/locations/global/publishers/google/models/gemini-3.8-flash:'


def benchmark():
    token = subprocess.run(['gcloud', 'auth', 'print-access-token'], capture_output=True, text=True, check=True).stdout.strip()
    rows = [{'currency': 'AUD', 'amount_cents': i * 123} for i in range(1, 201) if i % 3]
    expected = {'recordCount': len(rows), 'totalCents': sum(x['amount_cents'] for x in rows), 'currency': 'AUD'}
    question = 'What are the overdue invoice recordCount, totalCents and currency? Return only those JSON fields. Data: '
    inputs = {'row_payload': question + json.dumps(rows, separators=(',', ':')),
              'local_aggregate': question + json.dumps(expected, separators=(',', ':'))}
    counts = {}
    for name, text in inputs.items():
        data = {'contents': [{'role': 'user', 'parts': [{'text': text}]}]}
        req = urllib.request.Request(URL + 'countTokens', data=json.dumps(data).encode(),
                                     headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=30) as response:
            counts[name] = json.load(response)['totalTokens']
    reduction = round((1-counts['local_aggregate']/counts['row_payload'])*100, 2)
    report = {'dataset': '134 synthetic invoices; no supplier, banking or customer fields', 'model': 'gemini-3.8-flash',
              'location': 'global', 'method': 'Actual provider countTokens calls on equivalent-question input payloads',
              'counts': counts, 'inputTokenReductionPercent': reduction,
              'summary': f"Synthetic input payload: {counts['row_payload']:,} → {counts['local_aggregate']:,} provider-counted tokens ({reduction}% reduction). Excludes outputs, planning, infrastructure and net AUD costs.",
              'netCostReduction': None, 'outputQualityBenchmark': 'Not measured by countTokens; cloud workflow separately checks aggregate correctness'}
    (STATE / 'token-benchmark.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    benchmark()
