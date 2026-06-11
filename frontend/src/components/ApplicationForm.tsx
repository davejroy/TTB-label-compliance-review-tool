import type { ApplicationData } from "../types";

interface Props {
  value: ApplicationData;
  onChange: (value: ApplicationData) => void;
  idPrefix: string;
}

const FIELD_CLASSES =
  "w-full rounded-lg border border-slate-300 px-4 py-3 text-lg focus:border-[#15396a] focus:ring-2 focus:ring-blue-200 focus:outline-none";

const LABEL_CLASSES = "block text-base font-semibold text-slate-700 mb-1";

export default function ApplicationForm({ value, onChange, idPrefix }: Props) {
  function set<K extends keyof ApplicationData>(key: K, val: ApplicationData[K]) {
    onChange({ ...value, [key]: val });
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
      <div className="sm:col-span-2">
        <label className={LABEL_CLASSES} htmlFor={`${idPrefix}-beverage`}>
          Beverage Type
        </label>
        <select
          id={`${idPrefix}-beverage`}
          className={FIELD_CLASSES}
          value={value.beverage_type}
          onChange={(e) => set("beverage_type", e.target.value as ApplicationData["beverage_type"])}
        >
          <option value="distilled_spirits">Distilled Spirits</option>
          <option value="wine">Wine</option>
          <option value="beer">Beer</option>
        </select>
      </div>

      <div>
        <label className={LABEL_CLASSES} htmlFor={`${idPrefix}-brand`}>
          Brand Name
        </label>
        <input
          id={`${idPrefix}-brand`}
          className={FIELD_CLASSES}
          type="text"
          placeholder="e.g. Old Tom Distillery"
          value={value.brand_name}
          onChange={(e) => set("brand_name", e.target.value)}
        />
      </div>

      <div>
        <label className={LABEL_CLASSES} htmlFor={`${idPrefix}-class`}>
          Class / Type
        </label>
        <input
          id={`${idPrefix}-class`}
          className={FIELD_CLASSES}
          type="text"
          placeholder="e.g. Kentucky Straight Bourbon Whiskey"
          value={value.class_type}
          onChange={(e) => set("class_type", e.target.value)}
        />
      </div>

      <div>
        <label className={LABEL_CLASSES} htmlFor={`${idPrefix}-abv`}>
          Alcohol Content
        </label>
        <input
          id={`${idPrefix}-abv`}
          className={FIELD_CLASSES}
          type="text"
          placeholder="e.g. 45% Alc./Vol. (90 Proof)"
          value={value.alcohol_content}
          onChange={(e) => set("alcohol_content", e.target.value)}
        />
      </div>

      <div>
        <label className={LABEL_CLASSES} htmlFor={`${idPrefix}-net`}>
          Net Contents
        </label>
        <input
          id={`${idPrefix}-net`}
          className={FIELD_CLASSES}
          type="text"
          placeholder="e.g. 750 mL"
          value={value.net_contents}
          onChange={(e) => set("net_contents", e.target.value)}
        />
      </div>

      <div className="sm:col-span-2">
        <label className={LABEL_CLASSES} htmlFor={`${idPrefix}-address`}>
          Name and Address (optional)
        </label>
        <input
          id={`${idPrefix}-address`}
          className={FIELD_CLASSES}
          type="text"
          placeholder="e.g. Old Tom Distillery, Bardstown, KY"
          value={value.name_and_address ?? ""}
          onChange={(e) => set("name_and_address", e.target.value)}
        />
      </div>

      <div className="sm:col-span-2">
        <label className={LABEL_CLASSES} htmlFor={`${idPrefix}-origin`}>
          Country of Origin (imports only, optional)
        </label>
        <input
          id={`${idPrefix}-origin`}
          className={FIELD_CLASSES}
          type="text"
          placeholder="e.g. Product of Scotland"
          value={value.country_of_origin ?? ""}
          onChange={(e) => set("country_of_origin", e.target.value)}
        />
      </div>
    </div>
  );
}
