const DISTRICT_LABELS: Record<string, string> = {
  "central-metro": "Central Metro District",
  "north-division": "North Division",
  "south-division": "South Division",
  "east-division": "East Division",
  "west-division": "West Division",
  "harbor-zone": "Harbor & Transit Zone",
};

export function formatDistrictName(districtId: string | null | undefined): string {
  if (!districtId) return "—";
  const known = DISTRICT_LABELS[districtId];
  if (known) return known;
  return districtId
    .split("-")
    .map((part) => (part ? part[0].toUpperCase() + part.slice(1) : ""))
    .join(" ");
}
