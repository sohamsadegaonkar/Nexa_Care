import asyncio
import time
import httpx
import statistics
import argparse

async def poll_status(client, base_url, request_id, headers, interval, duration):
    latencies = []
    errors = 0
    end_time = time.time() + duration
    
    while time.time() < end_time:
        start = time.perf_counter()
        try:
            resp = await client.get(f"{base_url}/api/v2/push/{request_id}/status", headers=headers)
            latencies.append(time.perf_counter() - start)
            if resp.status_code != 200:
                errors += 1
        except Exception:
            errors += 1
        
        await asyncio.sleep(interval)
        
    return latencies, errors

async def run_load_test(base_url, sessions, interval, duration, token):
    headers = {"Authorization": f"Bearer {token}"}
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        # First, we'd need to create requests, but for testing polling we can use dummy IDs 
        # or actually hit the /request endpoint. Let's hit /request first for each session.
        tasks = []
        request_ids = []
        
        print(f"Initializing {sessions} sessions...")
        for i in range(sessions):
            try:
                # We need a valid patient_id for the rate limiter. 
                # Using unique patient IDs per session to avoid per-patient concurrency limits.
                resp = await client.post(
                    f"{base_url}/api/v2/push/request",
                    json={
                        "patient_id": f"00000000-0000-0000-0000-{i:012d}",
                        "provider_id": "doc-1",
                        "purpose": "load-test",
                        "scope": "clinical.*"
                    },
                    headers=headers
                )
                if resp.status_code == 201:
                    request_ids.append(resp.json()["request_id"])
                else:
                    print(f"Failed to create request for session {i}: {resp.status_code} {resp.text}")
            except Exception as e:
                print(f"Error creating request: {e}")

        if not request_ids:
            print("No request IDs created. Aborting.")
            return

        print(f"Starting load test on {len(request_ids)} active sessions for {duration}s...")
        
        for req_id in request_ids:
            tasks.append(poll_status(client, base_url, req_id, headers, interval, duration))
            
        results = await asyncio.gather(*tasks)
        
        all_latencies = []
        total_errors = 0
        total_requests = 0
        
        for lats, errs in results:
            all_latencies.extend(lats)
            total_errors += errs
            total_requests += len(lats) + errs

        if not all_latencies:
            print("No data collected.")
            return

        all_latencies.sort()
        count = len(all_latencies)
        
        p50 = statistics.median(all_latencies)
        p95 = all_latencies[int(count * 0.95)]
        p99 = all_latencies[int(count * 0.99)]
        
        print("\n--- Load Test Report ---")
        print(f"Total Requests: {total_requests}")
        print(f"Error Rate: {(total_errors/total_requests)*100:.2f}%")
        print(f"P50 Latency: {p50*1000:.2f}ms")
        print(f"P95 Latency: {p95*1000:.2f}ms")
        print(f"P99 Latency: {p99*1000:.2f}ms")
        
        # Decision Framework
        print("\n--- Decision Framework ---")
        if p99 < 0.1 and (total_errors/total_requests) < 0.001:
            print("RESULT: KEEP POLLING. Performance is sufficient.")
        elif p99 > 0.2 or (total_errors/total_requests) > 0.01:
            print("RESULT: SWITCH TO WEBSOCKETS. Performance thresholds exceeded.")
        else:
            print("RESULT: MARGINAL. Consider optimization or WebSocket implementation.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Push Polling Load Test")
    parser.add_argument("--url", default="http://localhost:8000", help="Base URL of the API")
    parser.add_argument("--sessions", type=int, default=50, help="Number of concurrent sessions")
    parser.add_argument("--interval", type=float, default=2.0, help="Polling interval in seconds")
    parser.add_argument("--duration", type=int, default=90, help="Test duration in seconds")
    parser.add_argument("--token", required=True, help="Valid provider session token")
    
    args = parser.parse_args()
    
    asyncio.run(run_load_test(args.url, args.sessions, args.interval, args.duration, args.token))
