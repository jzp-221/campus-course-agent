const COURSE_FIELDS = ['name', 'code', 'description', 'status']

// An empty string is an explicit edit; an omitted field is not an edit.
export function buildCoursePayload(form) {
  const payload = {}
  for (const field of COURSE_FIELDS) {
    if (Object.hasOwn(form, field) && form[field] !== undefined) {
      payload[field] = form[field]
    }
  }
  return payload
}
