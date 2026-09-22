//! Checked SUM and Decimal publication boundary. Other SQL arithmetic retains
//! engine semantics; this deliberately does not certify an entire expression.
use arrow::{array::*, datatypes::*, record_batch::RecordBatch};
use arrow_buffer::i256;
use datafusion_common::{DataFusionError, Result, ScalarValue};
use datafusion_expr::{
    Accumulator, AggregateUDFImpl, GroupsAccumulator, ReversedUDAF, Signature,
    function::{AccumulatorArgs, StateFieldsArgs},
    utils::AggregateOrderSensitivity,
};
use datafusion_expr_common::groups_accumulator::EmitTo;
use datafusion_functions_aggregate::sum::Sum;
use serde_json::{Value, json};
use std::{collections::HashMap, sync::Arc};

#[derive(Debug)]
pub struct NumericError {
    pub operation: String,
    pub data_type: String,
    pub expression: String,
    pub reason: &'static str,
}
impl std::fmt::Display for NumericError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "ARITHMETIC_OVERFLOW: {} {} ({}): {}",
            self.operation, self.expression, self.data_type, self.reason
        )
    }
}
impl std::error::Error for NumericError {}
impl NumericError {
    pub fn details(&self) -> Value {
        json!({"operation":self.operation,"data_type":self.data_type,"expression":self.expression,
            "reason":self.reason,"recovery":"Use a wider declared Decimal type where possible; otherwise reduce the range or use an external arbitrary-precision engine. No wrapped result was published for this batch."})
    }
}
fn error(op: &str, dt: &DataType, expr: &str, reason: &'static str) -> DataFusionError {
    DataFusionError::External(Box::new(NumericError {
        operation: op.into(),
        data_type: dt.to_string(),
        expression: expr.into(),
        reason,
    }))
}

pub fn policy() -> Value {
    json!({"policy":"rowtrail-numeric-v1","checked_sum":"Int64, UInt64, Decimal32/64/128/256; grouped, distinct, window and partial merge; checked i256 state, declared output range",
        "decimal_output":"declared precision checked before persistence and presentation",
        "other_sql_arithmetic":"engine semantics; finite-width and floating-point arithmetic are not certified",
        "accuracy_meaning":"exact describes sampling, not an arbitrary-precision arithmetic guarantee"})
}

#[derive(Debug, PartialEq, Eq, Hash)]
pub struct CheckedSum {
    inner: Sum,
}
impl CheckedSum {
    pub fn new() -> Self {
        Self { inner: Sum::new() }
    }
}
fn checked(dt: &DataType) -> bool {
    matches!(
        dt,
        DataType::Int64
            | DataType::UInt64
            | DataType::Decimal32(..)
            | DataType::Decimal64(..)
            | DataType::Decimal128(..)
            | DataType::Decimal256(..)
    )
}
impl AggregateUDFImpl for CheckedSum {
    fn name(&self) -> &str {
        "sum"
    }
    fn signature(&self) -> &Signature {
        self.inner.signature()
    }
    fn return_type(&self, types: &[DataType]) -> Result<DataType> {
        let dt = self.inner.return_type(types)?;
        if !checked(&dt) && dt != DataType::Float64 {
            return Err(DataFusionError::NotImplemented("SUM of interval/duration is outside the checked numeric contract; cast explicitly to numeric units".into()));
        }
        Ok(dt)
    }
    fn accumulator(&self, args: AccumulatorArgs) -> Result<Box<dyn Accumulator>> {
        if !checked(args.return_field.data_type()) {
            return self.inner.accumulator(args);
        }
        Ok(Box::new(CheckedAccumulator::new(
            args.return_field.data_type().clone(),
            args.name,
            args.is_distinct,
        )))
    }
    fn state_fields(&self, args: StateFieldsArgs) -> Result<Vec<FieldRef>> {
        if !checked(args.return_type()) {
            return self.inner.state_fields(args);
        }
        if args.is_distinct {
            Ok(vec![Arc::new(Field::new_list(
                format!("{}[checked_unique]", args.name),
                Field::new_list_field(DataType::Decimal256(76, 0), true),
                false,
            ))])
        } else {
            Ok(vec![
                Arc::new(Field::new(
                    format!("{}[checked_sum]", args.name),
                    DataType::Decimal256(76, 0),
                    false,
                )),
                Arc::new(Field::new(
                    format!("{}[checked_count]", args.name),
                    DataType::UInt64,
                    false,
                )),
            ])
        }
    }
    fn groups_accumulator_supported(&self, args: AccumulatorArgs) -> bool {
        !args.is_distinct
    }
    fn create_groups_accumulator(
        &self,
        args: AccumulatorArgs,
    ) -> Result<Box<dyn GroupsAccumulator>> {
        if !checked(args.return_field.data_type()) {
            return self.inner.create_groups_accumulator(args);
        }
        Ok(Box::new(CheckedGroups {
            range: Range::new(args.return_field.data_type().clone(), args.name),
            groups: Vec::new(),
        }))
    }
    fn create_sliding_accumulator(&self, args: AccumulatorArgs) -> Result<Box<dyn Accumulator>> {
        if !checked(args.return_field.data_type()) {
            return self.inner.create_sliding_accumulator(args);
        }
        self.accumulator(args)
    }
    fn reverse_expr(&self) -> ReversedUDAF {
        ReversedUDAF::Identical
    }
    fn order_sensitivity(&self) -> AggregateOrderSensitivity {
        AggregateOrderSensitivity::Insensitive
    }
    // Do not inherit SUM(x+c) rewrites: they change finite-width arithmetic.
}

#[derive(Debug)]
struct Range {
    dt: DataType,
    expression: String,
    min: i256,
    max: i256,
}
impl Range {
    fn new(dt: DataType, expression: &str) -> Self {
        let (min, max) = match dt {
            DataType::Int64 => (
                i256::from_i128(i64::MIN as i128),
                i256::from_i128(i64::MAX as i128),
            ),
            DataType::UInt64 => (i256::ZERO, i256::from_i128(u64::MAX as i128)),
            DataType::Decimal32(p, _)
            | DataType::Decimal64(p, _)
            | DataType::Decimal128(p, _)
            | DataType::Decimal256(p, _) => {
                let max = i256::from_i128(10).checked_pow(p.into()).unwrap() - i256::ONE;
                (-max, max)
            }
            _ => unreachable!(),
        };
        Self {
            dt,
            expression: expression.into(),
            min,
            max,
        }
    }
    fn fail(&self, reason: &'static str) -> DataFusionError {
        error("SUM", &self.dt, &self.expression, reason)
    }
    fn scalar(&self, state: State) -> Result<ScalarValue> {
        if state.count == 0 {
            return ScalarValue::try_from(&self.dt);
        }
        let value = state.sum;
        if value < self.min || value > self.max {
            return Err(self.fail("final sum exceeds declared output range"));
        }
        Ok(match self.dt {
            DataType::Int64 => ScalarValue::Int64(Some(value.to_i128().unwrap() as i64)),
            DataType::UInt64 => ScalarValue::UInt64(Some(value.to_i128().unwrap() as u64)),
            DataType::Decimal32(p, s) => {
                ScalarValue::Decimal32(Some(value.to_i128().unwrap() as i32), p, s)
            }
            DataType::Decimal64(p, s) => {
                ScalarValue::Decimal64(Some(value.to_i128().unwrap() as i64), p, s)
            }
            DataType::Decimal128(p, s) => {
                ScalarValue::Decimal128(Some(value.to_i128().unwrap()), p, s)
            }
            DataType::Decimal256(p, s) => ScalarValue::Decimal256(Some(value), p, s),
            _ => unreachable!(),
        })
    }
}
#[derive(Debug, Default, Clone, Copy)]
struct State {
    sum: i256,
    count: u64,
}
impl State {
    fn add(&mut self, value: i256, count: u64, range: &Range) -> Result<()> {
        self.sum = self
            .sum
            .checked_add(value)
            .ok_or_else(|| range.fail("i256 intermediate sum overflow"))?;
        self.count = self
            .count
            .checked_add(count)
            .ok_or_else(|| range.fail("row count overflow"))?;
        Ok(())
    }
    fn subtract(&mut self, value: i256, range: &Range) -> Result<()> {
        self.sum = self
            .sum
            .checked_sub(value)
            .ok_or_else(|| range.fail("i256 intermediate sum overflow"))?;
        self.count = self
            .count
            .checked_sub(1)
            .ok_or_else(|| range.fail("invalid window retraction"))?;
        Ok(())
    }
}
// Most numeric SUMs fit i128 even when the declared output does not. Fold
// batches there with checked addition, flushing into i256 only on overflow or
// batch end. This avoids i256 arithmetic per row without changing any limit.
fn add_batch(state: &mut State, array: &ArrayRef, range: &Range) -> Result<()> {
    macro_rules! fold {
        ($ty:ty,$convert:expr) => {{
            let a = array
                .as_any()
                .downcast_ref::<PrimitiveArray<$ty>>()
                .unwrap();
            if a.null_count() == 0 {
                // Independent checked lanes let the CPU overlap additions.
                // Merge in i256, so lanes never introduce a narrow final sum.
                let mut sums = [0i128; 4];
                let mut chunks = a.values().chunks_exact(4);
                for chunk in &mut chunks {
                    for (sum, value) in sums.iter_mut().zip(chunk) {
                        let value = ($convert)(*value);
                        if let Some(total) = sum.checked_add(value) {
                            *sum = total;
                        } else {
                            state.add(i256::from_i128(*sum), 0, range)?;
                            *sum = value;
                        }
                    }
                }
                for value in chunks.remainder() {
                    let value = ($convert)(*value);
                    if let Some(total) = sums[0].checked_add(value) {
                        sums[0] = total;
                    } else {
                        state.add(i256::from_i128(sums[0]), 0, range)?;
                        sums[0] = value;
                    }
                }
                for sum in sums {
                    state.add(i256::from_i128(sum), 0, range)?;
                }
                state.add(i256::ZERO, a.len() as u64, range)
            } else {
                let mut sum = 0i128;
                let mut count = 0u64;
                for v in a.iter().flatten() {
                    let value = ($convert)(v);
                    if let Some(total) = sum.checked_add(value) {
                        sum = total;
                    } else {
                        state.add(i256::from_i128(sum), count, range)?;
                        sum = value;
                        count = 0;
                    }
                    count += 1;
                }
                state.add(i256::from_i128(sum), count, range)
            }
        }};
    }
    match array.data_type() {
        DataType::Int64 => fold!(Int64Type, |v: i64| v as i128),
        DataType::UInt64 => fold!(UInt64Type, |v: u64| v as i128),
        DataType::Decimal32(..) => fold!(Decimal32Type, |v: i32| v as i128),
        DataType::Decimal64(..) => fold!(Decimal64Type, |v: i64| v as i128),
        DataType::Decimal128(..) => fold!(Decimal128Type, |v: i128| v),
        _ => values(array, |_, v| state.add(v, 1, range)),
    }
}

// One type dispatch per batch; no per-row ScalarValue allocation.
fn values(array: &ArrayRef, mut f: impl FnMut(usize, i256) -> Result<()>) -> Result<()> {
    macro_rules! visit {
        ($ty:ty,$convert:expr) => {{
            let a = array
                .as_any()
                .downcast_ref::<PrimitiveArray<$ty>>()
                .unwrap();
            for (i, v) in a.iter().enumerate() {
                if let Some(v) = v {
                    f(i, ($convert)(v))?;
                }
            }
        }};
    }
    match array.data_type() {
        DataType::Int64 => visit!(Int64Type, |v: i64| i256::from_i128(v as i128)),
        DataType::UInt64 => visit!(UInt64Type, |v: u64| i256::from_i128(v as i128)),
        DataType::Decimal32(..) => visit!(Decimal32Type, |v: i32| i256::from_i128(v as i128)),
        DataType::Decimal64(..) => visit!(Decimal64Type, |v: i64| i256::from_i128(v as i128)),
        DataType::Decimal128(..) => visit!(Decimal128Type, i256::from_i128),
        DataType::Decimal256(..) => visit!(Decimal256Type, |v| v),
        dt => {
            return Err(DataFusionError::Internal(format!(
                "unexpected checked SUM input {dt}"
            )));
        }
    }
    Ok(())
}
#[derive(Debug)]
struct CheckedAccumulator {
    range: Range,
    state: State,
    distinct: Option<HashMap<i256, u64>>,
}
impl CheckedAccumulator {
    fn new(dt: DataType, name: &str, distinct: bool) -> Self {
        Self {
            range: Range::new(dt, name),
            state: State::default(),
            distinct: distinct.then(HashMap::new),
        }
    }
    fn insert(&mut self, v: i256) -> Result<()> {
        if let Some(set) = &mut self.distinct {
            let count = set.entry(v).or_default();
            *count = count
                .checked_add(1)
                .ok_or_else(|| self.range.fail("distinct count overflow"))?;
            if *count > 1 {
                return Ok(());
            }
        }
        self.state.add(v, 1, &self.range)
    }
}
impl Accumulator for CheckedAccumulator {
    fn update_batch(&mut self, arrays: &[ArrayRef]) -> Result<()> {
        if self.distinct.is_none() {
            return add_batch(&mut self.state, &arrays[0], &self.range);
        }
        values(&arrays[0], |_, v| self.insert(v))
    }
    fn evaluate(&mut self) -> Result<ScalarValue> {
        self.range.scalar(self.state)
    }
    fn size(&self) -> usize {
        size_of::<Self>()
            + self.range.expression.capacity()
            + self.distinct.as_ref().map_or(0, |s| {
                (s.capacity() * 8 / 7 + 1) * (size_of::<(i256, u64)>() + 1) + 16
            })
    }
    fn state(&mut self) -> Result<Vec<ScalarValue>> {
        if let Some(set) = &self.distinct {
            let vals = set
                .keys()
                .map(|v| ScalarValue::Decimal256(Some(*v), 76, 0))
                .collect::<Vec<_>>();
            Ok(vec![ScalarValue::List(ScalarValue::new_list(
                &vals,
                &DataType::Decimal256(76, 0),
                true,
            ))])
        } else {
            Ok(vec![
                ScalarValue::Decimal256(Some(self.state.sum), 76, 0),
                ScalarValue::UInt64(Some(self.state.count)),
            ])
        }
    }
    fn merge_batch(&mut self, arrays: &[ArrayRef]) -> Result<()> {
        if self.distinct.is_some() {
            let lists = arrays[0].as_any().downcast_ref::<ListArray>().unwrap();
            for array in lists.iter().flatten() {
                values(&array, |_, v| self.insert(v))?;
            }
            Ok(())
        } else {
            let counts = arrays[1].as_any().downcast_ref::<UInt64Array>().unwrap();
            values(&arrays[0], |i, v| {
                self.state.add(v, counts.value(i), &self.range)
            })
        }
    }
    fn supports_retract_batch(&self) -> bool {
        true
    }
    fn retract_batch(&mut self, arrays: &[ArrayRef]) -> Result<()> {
        values(&arrays[0], |_, v| {
            if let Some(set) = &mut self.distinct {
                let count = set
                    .get_mut(&v)
                    .ok_or_else(|| self.range.fail("invalid distinct retraction"))?;
                *count -= 1;
                if *count > 0 {
                    return Ok(());
                }
                set.remove(&v);
            }
            self.state.subtract(v, &self.range)
        })
    }
}
struct CheckedGroups {
    range: Range,
    groups: Vec<State>,
}
impl GroupsAccumulator for CheckedGroups {
    fn update_batch(
        &mut self,
        arrays: &[ArrayRef],
        indices: &[usize],
        filter: Option<&BooleanArray>,
        total: usize,
    ) -> Result<()> {
        self.groups.resize(total, State::default());
        values(&arrays[0], |i, v| {
            if filter.is_none_or(|f| !f.is_null(i) && f.value(i)) {
                self.groups[indices[i]].add(v, 1, &self.range)?;
            }
            Ok(())
        })
    }
    fn evaluate(&mut self, emit: EmitTo) -> Result<ArrayRef> {
        let states = emit.take_needed(&mut self.groups);
        if states.is_empty() {
            return Ok(new_empty_array(&self.range.dt));
        }
        ScalarValue::iter_to_array(
            states
                .into_iter()
                .map(|s| self.range.scalar(s))
                .collect::<Result<Vec<_>>>()?,
        )
    }
    fn state(&mut self, emit: EmitTo) -> Result<Vec<ArrayRef>> {
        let states = emit.take_needed(&mut self.groups);
        Ok(vec![
            Arc::new(
                Decimal256Array::from_iter_values(states.iter().map(|s| s.sum))
                    .with_precision_and_scale(76, 0)?,
            ),
            Arc::new(UInt64Array::from_iter_values(
                states.iter().map(|s| s.count),
            )),
        ])
    }
    fn merge_batch(&mut self, arrays: &[ArrayRef], indices: &[usize], total: usize) -> Result<()> {
        self.groups.resize(total, State::default());
        let counts = arrays[1].as_any().downcast_ref::<UInt64Array>().unwrap();
        values(&arrays[0], |i, v| {
            self.groups[indices[i]].add(v, counts.value(i), &self.range)?;
            Ok(())
        })
    }
    fn convert_to_state(
        &self,
        arrays: &[ArrayRef],
        filter: Option<&BooleanArray>,
    ) -> Result<Vec<ArrayRef>> {
        let mut sums = vec![i256::ZERO; arrays[0].len()];
        let mut counts = vec![0u64; arrays[0].len()];
        values(&arrays[0], |i, v| {
            if filter.is_none_or(|f| !f.is_null(i) && f.value(i)) {
                sums[i] = v;
                counts[i] = 1;
            }
            Ok(())
        })?;
        Ok(vec![
            Arc::new(Decimal256Array::from_iter_values(sums).with_precision_and_scale(76, 0)?),
            Arc::new(UInt64Array::from(counts)),
        ])
    }
    fn size(&self) -> usize {
        size_of::<Self>()
            + self.groups.capacity() * size_of::<State>()
            + self.range.expression.capacity()
    }
}

fn has_decimal(dt: &DataType) -> bool {
    match dt {
        DataType::Decimal32(..)
        | DataType::Decimal64(..)
        | DataType::Decimal128(..)
        | DataType::Decimal256(..) => true,
        DataType::List(f)
        | DataType::LargeList(f)
        | DataType::FixedSizeList(f, _)
        | DataType::ListView(f)
        | DataType::LargeListView(f)
        | DataType::Map(f, _)
        | DataType::RunEndEncoded(_, f) => has_decimal(f.data_type()),
        DataType::Struct(fields) => fields.iter().any(|f| has_decimal(f.data_type())),
        DataType::Union(fields, _) => fields.iter().any(|(_, f)| has_decimal(f.data_type())),
        DataType::Dictionary(_, v) => has_decimal(v),
        _ => false,
    }
}
/// Check logically visible Decimal values, respecting parent nulls, offsets and
/// dictionary selection. Hidden child slots must not turn NULL into an error.
pub fn validate_array(array: &dyn Array, expression: &str) -> Result<()> {
    if !has_decimal(array.data_type()) {
        return Ok(());
    }
    macro_rules! decimal {
        ($ty:ty,$p:expr) => {
            array
                .as_any()
                .downcast_ref::<PrimitiveArray<$ty>>()
                .unwrap()
                .validate_decimal_precision($p)
                .map_err(|_| {
                    error(
                        "decimal_output",
                        array.data_type(),
                        expression,
                        "value exceeds declared Decimal precision",
                    )
                })?
        };
    }
    macro_rules! nested {
        ($ty:ty) => {{
            let values = array.as_any().downcast_ref::<$ty>().unwrap();
            for i in 0..values.len() {
                if !values.is_null(i) {
                    validate_array(values.value(i).as_ref(), expression)?;
                }
            }
        }};
    }
    macro_rules! dictionary {
        ($ty:ty) => {{
            let a = array
                .as_any()
                .downcast_ref::<DictionaryArray<$ty>>()
                .unwrap();
            let mut seen = vec![false; a.values().len()];
            for index in a.keys_iter().flatten() {
                if !seen[index] {
                    seen[index] = true;
                    validate_array(a.values().slice(index, 1).as_ref(), expression)?;
                }
            }
        }};
    }
    match array.data_type() {
        DataType::Decimal32(p, _) => decimal!(Decimal32Type, *p),
        DataType::Decimal64(p, _) => decimal!(Decimal64Type, *p),
        DataType::Decimal128(p, _) => decimal!(Decimal128Type, *p),
        DataType::Decimal256(p, _) => decimal!(Decimal256Type, *p),
        DataType::Struct(_) => {
            let a = array.as_any().downcast_ref::<StructArray>().unwrap();
            for child in a.columns() {
                if !has_decimal(child.data_type()) {
                    continue;
                }
                let nulls = arrow_buffer::NullBuffer::union(a.nulls(), child.nulls());
                let masked = make_array(child.to_data().into_builder().nulls(nulls).build()?);
                validate_array(masked.as_ref(), expression)?;
            }
        }
        DataType::List(_) => nested!(ListArray),
        DataType::LargeList(_) => nested!(LargeListArray),
        DataType::FixedSizeList(..) => nested!(FixedSizeListArray),
        DataType::ListView(_) => nested!(ListViewArray),
        DataType::LargeListView(_) => nested!(LargeListViewArray),
        DataType::Map(..) => {
            let a = array.as_any().downcast_ref::<MapArray>().unwrap();
            for i in 0..a.len() {
                if !a.is_null(i) {
                    validate_array(&a.value(i), expression)?;
                }
            }
        }
        DataType::Union(..) => nested!(UnionArray),
        DataType::Dictionary(k, _) => match k.as_ref() {
            DataType::Int8 => dictionary!(Int8Type),
            DataType::Int16 => dictionary!(Int16Type),
            DataType::Int32 => dictionary!(Int32Type),
            DataType::Int64 => dictionary!(Int64Type),
            DataType::UInt8 => dictionary!(UInt8Type),
            DataType::UInt16 => dictionary!(UInt16Type),
            DataType::UInt32 => dictionary!(UInt32Type),
            DataType::UInt64 => dictionary!(UInt64Type),
            _ => unreachable!(),
        },
        DataType::RunEndEncoded(run, _) => {
            let values = match run.data_type() {
                DataType::Int16 => array
                    .as_any()
                    .downcast_ref::<RunArray<Int16Type>>()
                    .unwrap()
                    .values_slice(),
                DataType::Int32 => array
                    .as_any()
                    .downcast_ref::<RunArray<Int32Type>>()
                    .unwrap()
                    .values_slice(),
                DataType::Int64 => array
                    .as_any()
                    .downcast_ref::<RunArray<Int64Type>>()
                    .unwrap()
                    .values_slice(),
                _ => unreachable!(),
            };
            validate_array(values.as_ref(), expression)?;
        }
        _ => {}
    }
    Ok(())
}

pub fn validate_batch(batch: &RecordBatch) -> Result<()> {
    for (field, array) in batch.schema().fields().iter().zip(batch.columns()) {
        validate_array(array.as_ref(), field.name())?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use datafusion::{datasource::MemTable, prelude::*};
    fn context() -> SessionContext {
        let ctx = SessionContext::new_with_config(SessionConfig::new().with_target_partitions(2));
        ctx.register_udaf(datafusion_expr::AggregateUDF::from(CheckedSum::new()));
        ctx
    }
    #[tokio::test]
    async fn checked_sum_scalar_distinct_and_window_boundaries() {
        let ctx = context();
        for sql in [
            "SELECT SUM(n) FROM (VALUES (9223372036854775807::BIGINT),(1::BIGINT)) v(n)",
            "SELECT SUM(n) FROM (VALUES ('-9223372036854775808'::BIGINT),(-1::BIGINT)) v(n)",
            "SELECT SUM(n) FROM (VALUES ('18446744073709551615'::BIGINT UNSIGNED),(1::BIGINT UNSIGNED)) v(n)",
            "SELECT SUM(DISTINCT n) FROM (VALUES (9223372036854775807::BIGINT),(1::BIGINT),(1::BIGINT)) v(n)",
            "SELECT SUM(n) OVER (ORDER BY i ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) FROM (VALUES (1,9223372036854775807::BIGINT),(2,1::BIGINT)) v(i,n)",
            "SELECT SUM(n) FROM (VALUES ('99999999999999999999999999999999999999'::DECIMAL(38,0)),(1::DECIMAL(38,0))) v(n)",
        ] {
            let e = ctx.sql(sql).await.unwrap().collect().await.unwrap_err();
            let e = anyhow::Error::new(e);
            assert_eq!(
                crate::errors::code(&e, "SQL_ERROR"),
                "ARITHMETIC_OVERFLOW",
                "{sql}: {e:#}"
            );
            assert_eq!(crate::errors::details(&e)["operation"], "SUM");
        }
        for (sql, expected) in [
            (
                "SELECT SUM(n) FROM (VALUES (9223372036854775807::BIGINT),(1::BIGINT),(-1::BIGINT)) v(n)",
                "9223372036854775807",
            ),
            (
                "SELECT SUM(DISTINCT n) FROM (VALUES (2::BIGINT),(2::BIGINT),(NULL::BIGINT),(-1::BIGINT)) v(n)",
                "1",
            ),
            (
                "SELECT SUM(n) FROM (VALUES (NULL::BIGINT),(NULL::BIGINT)) v(n)",
                "NULL",
            ),
            (
                "SELECT SUM(n) FROM (VALUES (9223372036854775807::DECIMAL(38,0)),(1::DECIMAL(38,0))) v(n)",
                "9223372036854775808",
            ),
        ] {
            let batches = ctx.sql(sql).await.unwrap().collect().await.unwrap();
            let value = ScalarValue::try_from_array(batches[0].column(0), 0).unwrap();
            assert_eq!(
                if value.is_null() {
                    "NULL".into()
                } else {
                    value.to_string()
                },
                expected,
                "{sql}"
            );
        }
    }
    #[tokio::test]
    async fn checked_partial_merge_groups_filters_and_retractions() {
        let ctx = context();
        let schema = Arc::new(Schema::new(vec![
            Field::new("g", DataType::Int64, false),
            Field::new("n", DataType::Int64, true),
        ]));
        let batch = |g: Vec<i64>, n: Vec<i64>| {
            RecordBatch::try_new(
                schema.clone(),
                vec![Arc::new(Int64Array::from(g)), Arc::new(Int64Array::from(n))],
            )
            .unwrap()
        };
        let table = MemTable::try_new(
            schema.clone(),
            vec![
                vec![batch(vec![1, 1, 2], vec![i64::MAX, 1, 2])],
                vec![batch(vec![1, 2, 2], vec![-1, 2, 3])],
            ],
        )
        .unwrap();
        ctx.register_table("t", Arc::new(table)).unwrap();
        let good=ctx.sql("SELECT g,SUM(n),SUM(DISTINCT n),SUM(n) FILTER(WHERE n < 4) FROM t GROUP BY g ORDER BY g").await.unwrap().collect().await.unwrap();
        assert_eq!(
            ScalarValue::try_from_array(good[0].column(1), 0).unwrap(),
            ScalarValue::Int64(Some(i64::MAX))
        );
        assert_eq!(
            ScalarValue::try_from_array(good[0].column(2), 1).unwrap(),
            ScalarValue::Int64(Some(5))
        );
        assert_eq!(
            ScalarValue::try_from_array(good[0].column(3), 0).unwrap(),
            ScalarValue::Int64(Some(0))
        );
        let e = ctx
            .sql("SELECT SUM(n) FROM t WHERE n>0")
            .await
            .unwrap()
            .collect()
            .await
            .unwrap_err();
        assert_eq!(
            crate::errors::code(&anyhow::Error::new(e), "SQL_ERROR"),
            "ARITHMETIC_OVERFLOW"
        );
        // Exercise each actual Decimal array/update/merge path, not just its
        // final range predicate. Each partition alone fits its declared type.
        for (index, dt) in [
            DataType::Decimal32(9, 2),
            DataType::Decimal64(18, 2),
            DataType::Decimal128(38, 0),
            DataType::Decimal256(76, 0),
        ]
        .into_iter()
        .enumerate()
        {
            let range = Range::new(dt.clone(), "decimal matrix");
            let schema = Arc::new(Schema::new(vec![Field::new("n", dt, true)]));
            let batch = |v| {
                RecordBatch::try_new(
                    schema.clone(),
                    vec![
                        range
                            .scalar(State { sum: v, count: 1 })
                            .unwrap()
                            .to_array()
                            .unwrap(),
                    ],
                )
                .unwrap()
            };
            let table = MemTable::try_new(
                schema.clone(),
                vec![vec![batch(range.max)], vec![batch(i256::ONE)]],
            )
            .unwrap();
            ctx.register_table(format!("d{index}"), Arc::new(table))
                .unwrap();
            let e = ctx
                .sql(&format!("SELECT SUM(n) FROM d{index}"))
                .await
                .unwrap()
                .collect()
                .await
                .unwrap_err();
            assert_eq!(
                crate::errors::code(&anyhow::Error::new(e), "SQL_ERROR"),
                "ARITHMETIC_OVERFLOW"
            );
        }
        let mut accumulator = CheckedAccumulator::new(DataType::Int64, "window", true);
        accumulator
            .update_batch(&[Arc::new(Int64Array::from(vec![2, 2, 3]))])
            .unwrap();
        accumulator
            .retract_batch(&[Arc::new(Int64Array::from(vec![2]))])
            .unwrap();
        assert_eq!(accumulator.evaluate().unwrap(), ScalarValue::Int64(Some(5)));
        accumulator
            .retract_batch(&[Arc::new(Int64Array::from(vec![2, 3]))])
            .unwrap();
        assert_eq!(accumulator.evaluate().unwrap(), ScalarValue::Int64(None));
    }
    #[test]
    fn nested_precision_respects_nulls_slices_and_dictionary_selection() {
        let values: ArrayRef = Arc::new(
            Decimal128Array::from(vec![100, 99])
                .with_precision_and_scale(2, 0)
                .unwrap(),
        );
        let field = Arc::new(Field::new("item", DataType::Decimal128(2, 0), true));
        let lists = ListArray::new(
            field.clone(),
            arrow_buffer::OffsetBuffer::new(vec![0, 1, 2].into()),
            values.clone(),
            Some(arrow_buffer::NullBuffer::from(vec![false, true])),
        );
        validate_array(&lists, "list").unwrap();
        validate_array(&lists.slice(0, 1), "list").unwrap();
        let structs = StructArray::new(
            vec![field.clone()].into(),
            vec![values.clone()],
            Some(arrow_buffer::NullBuffer::from(vec![false, true])),
        );
        validate_array(&structs, "struct").unwrap();
        let dictionary =
            DictionaryArray::<Int8Type>::try_new(Int8Array::from(vec![1, 1]), values.clone())
                .unwrap();
        validate_array(&dictionary, "dict").unwrap();
        let invalid = StructArray::new(vec![field].into(), vec![values], None);
        assert!(validate_array(&invalid, "struct").is_err());
    }

    #[test]
    fn decimal_precision_and_wide_state_are_checked_before_display() {
        let bad = Decimal128Array::from(vec![100])
            .with_precision_and_scale(2, 0)
            .unwrap();
        assert!(validate_array(&bad, "amount").is_err());
        let good = Decimal128Array::from(vec![Some(99), None, Some(-99)])
            .with_precision_and_scale(2, 0)
            .unwrap();
        validate_array(&good, "amount").unwrap();
        for dt in [
            DataType::Decimal32(9, 2),
            DataType::Decimal64(18, 2),
            DataType::Decimal128(38, 0),
            DataType::Decimal256(76, 0),
        ] {
            let range = Range::new(dt, "SUM(x)");
            assert!(
                range
                    .scalar(State {
                        sum: range.max,
                        count: 1
                    })
                    .is_ok()
            );
            assert!(
                range
                    .scalar(State {
                        sum: range.max + i256::ONE,
                        count: 1
                    })
                    .is_err()
            );
        }
        // Force intermediate i128 overflow in repeated lanes and the nullable
        // fallback, then cancel it; the representable final answer must survive.
        let max = 10i128.pow(38) - 1;
        let raw = std::iter::repeat_n(max, 8)
            .chain(std::iter::repeat_n(-max, 8))
            .chain([42])
            .collect::<Vec<_>>();
        for nullable in [false, true] {
            let values = raw
                .iter()
                .flat_map(|v| {
                    if nullable {
                        vec![Some(*v), None]
                    } else {
                        vec![Some(*v)]
                    }
                })
                .collect::<Vec<_>>();
            let array: ArrayRef = Arc::new(
                Decimal128Array::from(values)
                    .with_precision_and_scale(38, 0)
                    .unwrap(),
            );
            let mut sum = CheckedAccumulator::new(DataType::Decimal128(38, 0), "wide batch", false);
            sum.update_batch(&[array]).unwrap();
            assert_eq!(
                sum.evaluate().unwrap(),
                ScalarValue::Decimal128(Some(42), 38, 0)
            );
            assert_eq!(sum.state.count, 17);
        }
        let range = Range::new(DataType::Decimal256(76, 0), "SUM(x)");
        let mut state = State {
            sum: i256::MAX,
            count: 1,
        };
        assert!(state.add(i256::ONE, 1, &range).is_err());
    }
}
