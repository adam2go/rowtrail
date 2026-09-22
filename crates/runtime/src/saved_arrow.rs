//! Saved results are known IPC files, already bounded to 8 MiB per part.
//! Keep each part whole: byte-range repartitioning made concurrent partitions
//! repeatedly verify the same part and retained every selected batch range.
//! ListingTable still balances separate files across target partitions.
use arrow::datatypes::SchemaRef;
use datafusion_catalog::Session;
use datafusion_common::{Result, Statistics, tree_node::TreeNodeRecursion};
use datafusion_datasource::file_compression_type::FileCompressionType;
use datafusion_datasource::{
    TableSchema, file::FileSource, file_format::FileFormat, file_scan_config::FileScanConfig,
    file_stream::FileOpener, source::DataSourceExec,
};
use datafusion_datasource_arrow::{file_format::ArrowFormat, source::ArrowSource};
use datafusion_physical_expr::projection::ProjectionExprs;
use datafusion_physical_plan::{ExecutionPlan, PhysicalExpr, metrics::ExecutionPlanMetricsSet};
use object_store::{ObjectMeta, ObjectStore};
use std::sync::Arc;

#[derive(Debug)]
pub struct SavedArrowFormat;
#[async_trait::async_trait]
impl FileFormat for SavedArrowFormat {
    fn get_ext(&self) -> String {
        ArrowFormat.get_ext()
    }
    fn get_ext_with_compression(&self, c: &FileCompressionType) -> Result<String> {
        ArrowFormat.get_ext_with_compression(c)
    }
    fn compression_type(&self) -> Option<FileCompressionType> {
        None
    }
    async fn infer_schema(
        &self,
        state: &dyn Session,
        store: &Arc<dyn ObjectStore>,
        objects: &[ObjectMeta],
    ) -> Result<SchemaRef> {
        ArrowFormat.infer_schema(state, store, objects).await
    }
    async fn infer_stats(
        &self,
        _: &dyn Session,
        _: &Arc<dyn ObjectStore>,
        schema: SchemaRef,
        _: &ObjectMeta,
    ) -> Result<Statistics> {
        Ok(Statistics::new_unknown(&schema))
    }
    async fn create_physical_plan(
        &self,
        _: &dyn Session,
        config: FileScanConfig,
    ) -> Result<Arc<dyn ExecutionPlan>> {
        // The runtime wrote these as IPC files. Opening/verification still takes
        // place on execution, even if no format sniff is needed during planning.
        Ok(DataSourceExec::from_data_source(config))
    }
    fn file_source(&self, schema: TableSchema) -> Arc<dyn FileSource> {
        Arc::new(WholeParts(Arc::new(ArrowSource::new_file_source(schema))))
    }
}
#[derive(Clone)]
struct WholeParts(Arc<dyn FileSource>);
impl FileSource for WholeParts {
    fn create_file_opener(
        &self,
        store: Arc<dyn ObjectStore>,
        config: &FileScanConfig,
        partition: usize,
    ) -> Result<Arc<dyn FileOpener>> {
        self.0.create_file_opener(store, config, partition)
    }
    fn table_schema(&self) -> &TableSchema {
        self.0.table_schema()
    }
    fn with_batch_size(&self, n: usize) -> Arc<dyn FileSource> {
        Arc::new(Self(self.0.with_batch_size(n)))
    }
    fn metrics(&self) -> &ExecutionPlanMetricsSet {
        self.0.metrics()
    }
    fn file_type(&self) -> &str {
        "rowtrail_arrow"
    }
    fn supports_repartitioning(&self) -> bool {
        false
    }
    fn projection(&self) -> Option<&ProjectionExprs> {
        self.0.projection()
    }
    fn try_pushdown_projection(&self, p: &ProjectionExprs) -> Result<Option<Arc<dyn FileSource>>> {
        Ok(self
            .0
            .try_pushdown_projection(p)?
            .map(|s| Arc::new(Self(s)) as Arc<dyn FileSource>))
    }
    fn apply_expressions(
        &self,
        f: &mut dyn FnMut(&Arc<dyn PhysicalExpr>) -> Result<TreeNodeRecursion>,
    ) -> Result<TreeNodeRecursion> {
        self.0.apply_expressions(f)
    }
}
