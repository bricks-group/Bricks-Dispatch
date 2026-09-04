/** Canada RE amenity/feature presets for TourTagEditor chips.
 * Keep in sync: web/src/lib/amenityPresets.ts, ios-swift TourTagEditor.
 * Chips only fill the label field — never auto-add onto the tour.
 */
export const AMENITY_PRESETS: readonly string[] = [
  "Chef's kitchen",
  'Kitchen island',
  'Quartz counters',
  'Stainless appliances',
  'Walk-in closet',
  'Ensuite',
  'Soaker tub',
  'Hardwood floors',
  'Fireplace',
  'Open concept',
  'Home office',
  'Finished basement',
  'Laundry',
  'Deck / patio',
  'Pool',
  'Garage',
] as const;
