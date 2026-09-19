mod api;
mod coordinator;
mod db;
mod engine;
mod errors;
mod model;
mod probe;
mod results;
mod sources;
mod store;
mod worker;
use clap::{Parser, Subcommand};

#[derive(Parser)]
struct Args {
    #[command(subcommand)]
    command: Command,
}
#[derive(Subcommand)]
enum Command {
    Probe {
        #[arg(long)]
        directory: std::path::PathBuf,
        #[arg(long, default_value_t = 262144)]
        rows: usize,
    },
    Serve {
        #[arg(long)]
        workspace: std::path::PathBuf,
    },
    Worker {
        #[arg(long)]
        token: String,
    },
    Fixtures {
        #[arg(long)]
        directory: std::path::PathBuf,
        #[arg(long, default_value_t = 16384)]
        rows: usize,
    },
    Benchmark {
        #[arg(long)]
        source: std::path::PathBuf,
    },
}
#[tokio::main(worker_threads = 2)]
async fn main() -> anyhow::Result<()> {
    unsafe {
        libc::umask(0o077);
    }
    match Args::parse().command {
        Command::Probe { directory, rows } => probe::run(&directory, rows).await,
        Command::Fixtures { directory, rows } => probe::generate(&directory, rows),
        Command::Benchmark { source } => probe::baseline(&source).await,
        Command::Serve { workspace } => coordinator::serve(&workspace).await,
        Command::Worker { token } => {
            let previous = std::panic::take_hook();
            std::panic::set_hook(Box::new(move |info| {
                previous(info);
                std::process::exit(101)
            }));
            let result = worker::run(&token).await;
            if let Err(e) = &result {
                eprintln!("worker failed: {e:#}");
            }
            std::process::exit(if result.is_ok() { 0 } else { 1 });
        }
    }
}
