export type Industry = { value: string; label: string; group: string }

export const INDUSTRIES: Industry[] = [
  // Roofing / exterior (primary)
  { value: 'roofing contractor', label: 'Roofing contractor', group: 'Exterior' },
  { value: 'roof repair', label: 'Roof repair', group: 'Exterior' },
  { value: 'gutter installation', label: 'Gutter installation', group: 'Exterior' },
  { value: 'siding contractor', label: 'Siding contractor', group: 'Exterior' },
  { value: 'window installation', label: 'Window installation', group: 'Exterior' },
  { value: 'exterior painting', label: 'Exterior painting', group: 'Exterior' },

  // Storm / restoration
  { value: 'storm damage restoration', label: 'Storm damage restoration', group: 'Restoration' },
  { value: 'water damage restoration', label: 'Water damage restoration', group: 'Restoration' },
  { value: 'fire damage restoration', label: 'Fire damage restoration', group: 'Restoration' },
  { value: 'mold remediation', label: 'Mold remediation', group: 'Restoration' },

  // Home services
  { value: 'hvac contractor', label: 'HVAC contractor', group: 'Home Services' },
  { value: 'plumber', label: 'Plumber', group: 'Home Services' },
  { value: 'electrician', label: 'Electrician', group: 'Home Services' },
  { value: 'solar panel installation', label: 'Solar installation', group: 'Home Services' },
  { value: 'pest control', label: 'Pest control', group: 'Home Services' },
  { value: 'landscaping', label: 'Landscaping', group: 'Home Services' },
  { value: 'tree service', label: 'Tree service', group: 'Home Services' },
  { value: 'fence contractor', label: 'Fence contractor', group: 'Home Services' },
  { value: 'concrete contractor', label: 'Concrete contractor', group: 'Home Services' },
  { value: 'pool contractor', label: 'Pool contractor', group: 'Home Services' },

  // Remodeling
  { value: 'general contractor', label: 'General contractor', group: 'Remodeling' },
  { value: 'kitchen remodeling', label: 'Kitchen remodeling', group: 'Remodeling' },
  { value: 'bathroom remodeling', label: 'Bathroom remodeling', group: 'Remodeling' },
  { value: 'home builder', label: 'Home builder', group: 'Remodeling' },
]
