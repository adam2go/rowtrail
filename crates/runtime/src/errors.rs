//! Classify actual error causes, never arbitrary substrings in SQL or field names.
pub fn code(error: &anyhow::Error, fallback: &'static str) -> &'static str {
    for cause in error.chain() {
        if matches!(
            cause.downcast_ref::<datafusion::common::DataFusionError>(),
            Some(datafusion::common::DataFusionError::ResourcesExhausted(_))
        ) {
            return "RESOURCE_EXHAUSTED";
        }
        let message = cause.to_string();
        for code in [
            "IDEMPOTENCY_CONFLICT",
            "SOURCE_CHANGED",
            "RESULT_CORRUPT",
            "RESULT_UNAVAILABLE",
            "RESULT_NOT_READY",
            "OBJECT_NOT_FOUND",
            "OBJECT_EXPIRED",
            "OUTPUT_BUDGET_TOO_SMALL",
            "INVALID_CURSOR",
            "INVALID_ARGUMENT",
            "SCHEMA_CONFLICT",
            "SOURCE_DISCOVERY_LIMIT",
            "UNSUPPORTED_OPERATION",
            "RESOURCE_EXHAUSTED",
            "PROTOCOL_FRAME_TOO_LARGE",
            "CANCELLED",
            "BUDGET_EXHAUSTED",
        ] {
            if message == code || message.starts_with(&format!("{code}:")) {
                return code;
            }
        }
    }
    fallback
}

#[cfg(test)]
mod tests {
    use super::code;
    use datafusion::common::DataFusionError;

    #[test]
    fn native_engine_exhaustion_survives_context() {
        let error = anyhow::Error::new(DataFusionError::ResourcesExhausted("memory pool".into()))
            .context("executing query");
        assert_eq!(code(&error, "SQL_ERROR"), "RESOURCE_EXHAUSTED");
    }

    #[test]
    fn sql_messages_cannot_impersonate_source_or_resource_errors() {
        for text in [
            "SOURCE_CHANGED: user value",
            "RESOURCE_EXHAUSTED: user value",
        ] {
            let error = anyhow::Error::new(DataFusionError::Plan(text.into()));
            assert_eq!(code(&error, "SQL_ERROR"), "SQL_ERROR");
        }
        let changed = anyhow::anyhow!("SOURCE_CHANGED: file identity").context("reading source");
        assert_eq!(code(&changed, "SQL_ERROR"), "SOURCE_CHANGED");
    }
}
