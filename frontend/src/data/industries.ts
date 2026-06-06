export type Industry = { value: string; label: string; group: string }

// Broad industry buckets. Each bucket id matches a key in the backend's
// INDUSTRY_NAME_FILTERS (for post-scrape filtering) and INDUSTRY_SEARCH_QUERIES
// (for fan-out into multiple Google Maps search queries per bucket).
export const INDUSTRIES: Industry[] = [
  { value: 'roofing', label: 'Roofing & Exterior', group: 'Exterior' },
  { value: 'restoration', label: 'Storm / Restoration', group: 'Restoration' },
  { value: 'hvac', label: 'HVAC', group: 'Home Services' },
  { value: 'plumbing', label: 'Plumbing', group: 'Home Services' },
  { value: 'electrical', label: 'Electrical', group: 'Home Services' },
  { value: 'solar', label: 'Solar', group: 'Home Services' },
  { value: 'pest_control', label: 'Pest Control', group: 'Home Services' },
  { value: 'landscaping', label: 'Landscaping / Tree', group: 'Home Services' },
  { value: 'painting', label: 'Painting', group: 'Home Services' },
  { value: 'flooring', label: 'Flooring', group: 'Home Services' },
]
