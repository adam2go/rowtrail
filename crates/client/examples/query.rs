//! Run with ROWTRAIL_RUNTIME pointing to the matching runtime executable.
use rowtrail_client::Client;
use rowtrail_contracts::Request;
use serde_json::json;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let workspace = std::env::args()
        .nth(1)
        .unwrap_or_else(|| ".rowtrail".into());
    let client = Client::new(workspace)?;
    let response=client.call(&Request::new("query",json!({"bindings":{},"sql":"SELECT CAST(9007199254740993 AS BIGINT) id","execution":{"wait_ms":1000}}))).await?;
    anyhow::ensure!(response.ok, "query was not accepted: {:?}", response.error);
    let mut result = response.result.unwrap();
    loop {
        let state = result["job"]["state"].as_str().unwrap();
        if rowtrail_contracts::terminal(state) {
            anyhow::ensure!(state == "completed", "job failed: {result}");
            break;
        }
        result = client
            .call(&Request::new(
                "control",
                json!({"action":"wait","ref":result["job"]["id"],"wait_ms":1000}),
            ))
            .await?
            .result
            .unwrap();
    }
    let read=client.call(&Request::new("read",json!({"result_ref":result["job"]["result_ref"],"revision":result["readable_revision"]}))).await?;
    anyhow::ensure!(read.ok, "result read failed: {:?}", read.error);
    anyhow::ensure!(
        read.result.as_ref().unwrap()["rows"] == json!([["9007199254740993"]]),
        "integer precision lost"
    );
    println!("{}", serde_json::to_string(&read)?);
    Ok(())
}
