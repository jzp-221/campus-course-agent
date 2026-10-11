import test from 'node:test'
import assert from 'node:assert/strict'
import {
  SUPPORTED_DOCUMENT_SUFFIXES, validateDocumentFiles, documentStatus,
  isReindexDisabled, submitDocumentBatch, createDocumentActionRunner
} from '../src/utils/documentManagement.js'

const file = (name) => new File(['文档原文'], name)
const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

test('the document whitelist is exactly txt/md/pdf/docx and accepts case-insensitive suffixes', () => {
  assert.deepEqual([...SUPPORTED_DOCUMENT_SUFFIXES], ['.txt', '.md', '.pdf', '.docx'])
  assert.equal(validateDocumentFiles(['a.txt', 'b.MD', 'c.PDF', 'd.Docx'].map(file)).ok, true)
})

test('an unsupported member rejects the whole batch and names every invalid file', () => {
  const result = validateDocumentFiles([file('正常资料.md'), file('课程图.PNG'), file('旧版.doc'), file('无后缀')])
  assert.equal(result.ok, false)
  assert.deepEqual(result.invalidNames, ['课程图.PNG', '旧版.doc', '无后缀'])
  for (const name of result.invalidNames) assert.ok(result.message.includes(name))
  for (const suffix of SUPPORTED_DOCUMENT_SUFFIXES) assert.ok(result.message.includes(suffix))
})

test('validation accepts Element Plus raw files and rejects an empty selection', () => {
  const selected = file('课程指南.md')
  assert.equal(validateDocumentFiles([{ name: selected.name, raw: selected }]).ok, true)
  assert.equal(validateDocumentFiles([]).ok, false)
})

test('document state labels distinguish indexed, processing and failed without implying failed is searchable', () => {
  assert.deepEqual(documentStatus('indexed'), { label: '已入库可检索', type: 'success' })
  assert.deepEqual(documentStatus('processing'), { label: '重建中', type: 'primary' })
  assert.deepEqual(documentStatus('failed'), { label: '失败', type: 'danger' })
})

test('processing and local pending state disable reindex; indexed and failed permit a retry', () => {
  assert.equal(isReindexDisabled({ id: 17, status: 'processing' }, new Set()), true)
  assert.equal(isReindexDisabled({ id: 17, status: 'indexed' }, new Set([17])), true)
  assert.equal(isReindexDisabled({ id: 17, status: 'failed' }, true), true)
  assert.equal(isReindexDisabled({ id: 17, status: 'indexed' }, new Set([18])), false)
  assert.equal(isReindexDisabled({ id: 17, status: 'failed' }, false), false)
})

test('the batch submitter invokes the existing upload only once with all raw files and progress options', async () => {
  const first = file('实验规定.md'), second = file('考试要求.pdf')
  const calls = []
  const options = { onProgress() {} }
  const response = { code: 200, data: [{ id: 101 }, { id: 102 }] }
  const result = await submitDocumentBatch([{ raw: first, name: first.name }, second], async (files, config) => {
    calls.push({ files, config }); return response
  }, options)
  assert.equal(result, response)
  assert.equal(calls.length, 1)
  assert.deepEqual(calls[0].files, [first, second])
  assert.equal(calls[0].config, options)
})

test('mixed md/png and empty batches never submit any subset to the backend', async () => {
  let calls = 0
  const upload = async () => { calls++; return { code: 200 } }
  await assert.rejects(submitDocumentBatch([file('正常.md'), file('坏文件.png')], upload), /坏文件\.png/)
  await assert.rejects(submitDocumentBatch([], upload))
  assert.equal(calls, 0)
})

test('a pending action is locked synchronously, including confirmation, so double clicking performs one operation', async () => {
  const pending = new Set(), gate = deferred()
  const run = createDocumentActionRunner(pending)
  let calls = 0
  const operation = async () => { calls++; await gate.promise; return { code: 200 } }
  const first = run(17, operation)
  assert.equal(pending.has(17), true)
  assert.deepEqual(await run(17, operation), { skipped: true })
  assert.equal(calls, 1)
  gate.resolve()
  assert.deepEqual(await first, { skipped: false, response: { code: 200 } })
  assert.equal(pending.has(17), false)
  await run(17, operation)
  assert.equal(calls, 2)
})

test('a failed or cancelled document action releases its row lock and other documents remain independent', async () => {
  const pending = new Set(), gate = deferred()
  const run = createDocumentActionRunner(pending)
  const first = run(17, () => gate.promise)
  assert.deepEqual(await run(18, async () => 'ok'), { skipped: false, response: 'ok' })
  assert.equal(pending.has(17), true)
  assert.equal(pending.has(18), false)
  gate.reject(new Error('重新入库失败'))
  await assert.rejects(first, /重新入库失败/)
  assert.equal(pending.has(17), false)
  assert.deepEqual(await run(17, async () => 'retry'), { skipped: false, response: 'retry' })
})
