"""Exercise the real rmcp stdio handshake, discovery, and generated schema."""
import json, subprocess, sys, time, tempfile, pathlib, os, signal

temporary = tempfile.TemporaryDirectory(prefix="rowtrail-mcp-")
workspace = pathlib.Path(temporary.name) / "workspace"
p = subprocess.Popen([sys.argv[1], "--workspace", str(workspace), "mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
def send(value):
    p.stdin.write(json.dumps(value) + "\n")
    p.stdin.flush()
def receive(expected):
    while True:
        value = json.loads(p.stdout.readline())
        if value.get("id") == expected:
            return value
try:
    send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"rowtrail-probe","version":"1"}}})
    initialized = receive(1)
    assert "result" in initialized, initialized
    send({"jsonrpc":"2.0","method":"notifications/initialized"})
    send({"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}})
    discovered = receive(2)
    tools = discovered["result"]["tools"]
    query = next(t for t in tools if t["name"] == "data_query")
    assert query["inputSchema"]["type"] == "object"
    assert "sql" in query["inputSchema"]["properties"]
    assert query["inputSchema"]["additionalProperties"] is False
    send({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"data_query","arguments":{"bindings":{},"sql":"SELECT CAST(9007199254740993 AS BIGINT) id","execution":{"wait_ms":1000},"_request":{"idempotency_key":"mcp-query"}}}})
    computed = receive(3)["result"]["structuredContent"]
    assert computed['ok'],computed
    result = computed['result']
    job = result['job']['id']
    for index in range(20):
        if result['job']['state']=='completed': break
        send({"jsonrpc":"2.0","id":10+index,"method":"tools/call","params":{"name":"data_control","arguments":{"action":"wait","ref":job,"wait_ms":1000}}})
        result = receive(10+index)['result']['structuredContent']['result']
    assert result['job']['state']=='completed',result
    # CLI consumes the exact result version produced by MCP.
    read = json.loads(subprocess.check_output([sys.argv[1],'--workspace',str(workspace),'read',result['job']['result_ref'],'--revision',str(result['readable_revision'])]))
    assert read['result']['rows']==[['9007199254740993']],read
    send({"jsonrpc":"2.0","id":40,"method":"tools/call","params":{"name":"data_snapshot","arguments":{
        "source":{"result_ref":result['job']['result_ref'],"revision":result['readable_revision']},
        "label":"mcp snapshot","provenance":{"description":"Cross-entry independent snapshot"},"execution":{"wait_ms":1000}}}})
    snap=receive(40)['result']['structuredContent']['result']
    for i in range(20):
        if snap['job']['state']=='completed':break
        send({"jsonrpc":"2.0","id":50+i,"method":"tools/call","params":{"name":"data_control","arguments":{"action":"wait","ref":snap['job']['id'],"wait_ms":1000}}})
        snap=receive(50+i)['result']['structuredContent']['result']
    assert snap['job']['state']=='completed' and 'dataset_ref' in snap['binding'] and 'read' not in snap['next_actions'],snap
    answer=json.loads(subprocess.check_output([sys.argv[1],'--workspace',str(workspace),'call','query'],
        input=json.dumps({'sql':'SELECT id FROM t','bindings':{'t':snap['binding']},'execution':{'wait_ms':1000}}),text=True))
    assert answer['result']['observation']['rows']==[['9007199254740993']],answer
    print(json.dumps({"protocol":initialized["result"]["protocolVersion"],"tools":[t["name"] for t in tools],"query_schema_valid":True,"mcp_query_cli_read":True,"mcp_snapshot_cli_query":True}))
finally:
    p.stdin.close()
    try: p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
    if workspace.exists():
        try:
            doctor=json.loads(subprocess.check_output([sys.argv[1],'--workspace',str(workspace),'doctor']))
            os.kill(doctor['result']['coordinator_pid'],signal.SIGTERM)
        except (KeyError,ProcessLookupError,subprocess.CalledProcessError):pass
    temporary.cleanup()
