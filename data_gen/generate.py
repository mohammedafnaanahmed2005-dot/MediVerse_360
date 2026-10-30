"""Deterministic synthetic data generator for MediVerse 360.

All records are fictional. Names are drawn from small generic lists and every
patient id is prefixed with ``SYN`` so data can never be confused with real
hospital records. A small, controlled fraction of defects is injected so the
data-quality pipeline has something to catch.

Usage:
    python data_gen/generate.py [--out data/synthetic] [--seed 360] [--patients 4000]
"""

from __future__ import annotations

import argparse
import csv
import random
from datetime import date, datetime, timedelta
from pathlib import Path

SPECIALTIES = [
    ("SP01", "Cardiology"), ("SP02", "Neurology"), ("SP03", "Oncology"),
    ("SP04", "Orthopedics"), ("SP05", "Pediatrics"), ("SP06", "Psychiatry"),
    ("SP07", "Dermatology"), ("SP08", "Gastroenterology"), ("SP09", "Nephrology"),
    ("SP10", "Pulmonology"), ("SP11", "Endocrinology"), ("SP12", "Obstetrics & Gynecology"),
    ("SP13", "Ophthalmology"), ("SP14", "ENT"), ("SP15", "Urology"),
    ("SP16", "General Surgery"), ("SP17", "Emergency Medicine"), ("SP18", "Hematology"),
    ("SP19", "Rheumatology"), ("SP20", "Infectious Disease"),
]
FIRST = ["Alex", "Sam", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Avery", "Quinn",
         "Reese", "Drew", "Jamie", "Rowan", "Sky", "Parker", "Logan", "Emerson", "Hayden",
         "Kai", "Noor", "Aarav", "Mei", "Zara", "Omar", "Lena", "Ivan", "Ana", "Ravi"]
LAST = ["Synthson", "Demoley", "Testwood", "Mockford", "Samplewell", "Fakeridge",
        "Placeholt", "Dummond", "Exampleton", "Fictor", "Simmons-Syn", "Modelli"]
CITIES = ["Northvale", "Eastbrook", "Westford", "Southport", "Lakeside", "Hillcrest"]
BLOOD = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]
ALLERGENS = ["Penicillin", "Peanuts", "Latex", "Sulfa drugs", "Shellfish", "Aspirin", "Pollen"]
DIAG = {  # specialty -> (icd10, description)
    "SP01": [("I10", "Essential hypertension"), ("I25.10", "Coronary artery disease")],
    "SP02": [("G43.909", "Migraine"), ("G40.909", "Epilepsy")],
    "SP03": [("C50.919", "Breast neoplasm"), ("C34.90", "Lung neoplasm")],
    "SP04": [("M17.11", "Knee osteoarthritis"), ("S72.001A", "Femur fracture")],
    "SP05": [("J06.9", "Upper respiratory infection"), ("R50.9", "Fever")],
    "SP06": [("F32.9", "Major depressive disorder"), ("F41.1", "Generalized anxiety disorder")],
    "SP07": [("L40.0", "Psoriasis"), ("L20.9", "Atopic dermatitis")],
    "SP08": [("K21.9", "GERD"), ("K50.90", "Crohn disease")],
    "SP09": [("N18.3", "Chronic kidney disease stage 3"), ("N17.9", "Acute kidney injury")],
    "SP10": [("J45.909", "Asthma"), ("J44.9", "COPD")],
    "SP11": [("E11.9", "Type 2 diabetes"), ("E03.9", "Hypothyroidism")],
    "SP12": [("O80", "Normal delivery"), ("N80.9", "Endometriosis")],
    "SP13": [("H25.9", "Cataract"), ("H40.9", "Glaucoma")],
    "SP14": [("J32.9", "Chronic sinusitis"), ("H66.90", "Otitis media")],
    "SP15": [("N20.0", "Kidney stone"), ("N40.0", "BPH")],
    "SP16": [("K35.80", "Acute appendicitis"), ("K40.90", "Inguinal hernia")],
    "SP17": [("T14.90", "Injury, unspecified"), ("R07.9", "Chest pain")],
    "SP18": [("D50.9", "Iron deficiency anemia"), ("D69.6", "Thrombocytopenia")],
    "SP19": [("M06.9", "Rheumatoid arthritis"), ("M32.9", "Lupus")],
    "SP20": [("A41.9", "Sepsis"), ("B20", "HIV disease")],
}
LAB_TESTS = [  # code, name, unit, low, high, specimen
    ("HGB", "Hemoglobin", "g/dL", 12.0, 17.5, "Blood"),
    ("WBC", "White blood cell count", "10^9/L", 4.0, 11.0, "Blood"),
    ("PLT", "Platelet count", "10^9/L", 150, 400, "Blood"),
    ("GLU", "Fasting glucose", "mg/dL", 70, 99, "Blood"),
    ("HBA1C", "HbA1c", "%", 4.0, 5.6, "Blood"),
    ("CREAT", "Creatinine", "mg/dL", 0.6, 1.3, "Blood"),
    ("ALT", "Alanine aminotransferase", "U/L", 7, 56, "Blood"),
    ("TSH", "Thyroid stimulating hormone", "mIU/L", 0.4, 4.0, "Blood"),
    ("CRP", "C-reactive protein", "mg/L", 0, 10, "Blood"),
    ("NA", "Sodium", "mmol/L", 135, 145, "Blood"),
    ("K", "Potassium", "mmol/L", 3.5, 5.1, "Blood"),
    ("UPROT", "Urine protein", "mg/dL", 0, 14, "Urine"),
]
MODALITIES = [("MRI", ["Brain", "Spine", "Knee"]), ("CT", ["Chest", "Abdomen", "Head"]),
              ("XRAY", ["Chest", "Hand", "Femur"]), ("US", ["Abdomen", "Pelvis", "Thyroid"])]
PROCEDURES = [("Appendectomy", "SP16"), ("Hernia repair", "SP16"), ("Knee replacement", "SP04"),
              ("CABG", "SP01"), ("Cataract extraction", "SP13"), ("Cesarean section", "SP12"),
              ("Lumpectomy", "SP03"), ("Nephrolithotomy", "SP15"), ("Tonsillectomy", "SP14")]
DRUG_NAMES = ["Amoxicillin", "Metformin", "Lisinopril", "Atorvastatin", "Omeprazole",
              "Salbutamol", "Levothyroxine", "Sertraline", "Paracetamol", "Ibuprofen",
              "Ceftriaxone", "Insulin glargine", "Warfarin", "Prednisone", "Amlodipine",
              "Azithromycin", "Ondansetron", "Furosemide", "Heparin", "Morphine"]
EQUIPMENT = ["MRI scanner", "CT scanner", "Ventilator", "Infusion pump", "ECG monitor",
             "Ultrasound unit", "X-ray unit", "Defibrillator", "Dialysis machine", "Autoclave"]
START = date(2023, 1, 1)
END = date(2025, 12, 31)


class Gen:
    def __init__(self, seed: int, n_patients: int, defect_rate: float):
        self.r = random.Random(seed)
        self.n_patients = n_patients
        self.defect_rate = defect_rate
        self.tables: dict[str, list[dict]] = {}

    # helpers -------------------------------------------------------------
    def d(self, a: date = START, b: date = END) -> date:
        return a + timedelta(days=self.r.randint(0, (b - a).days))

    def name(self) -> tuple[str, str]:
        return self.r.choice(FIRST), self.r.choice(LAST)

    def defect(self) -> bool:
        return self.r.random() < self.defect_rate

    def phone(self) -> str:
        return f"+1-555-{self.r.randint(100, 999)}-{self.r.randint(1000, 9999)}"

    # generators ----------------------------------------------------------
    def run(self) -> dict[str, list[dict]]:
        r = self.r
        t = self.tables
        t["specialties"] = [{"specialty_id": s, "specialty_name": n} for s, n in SPECIALTIES]
        t["departments"] = [
            {"department_id": f"D{i:02d}", "department_name": f"{n} Department",
             "specialty_id": s, "floor": r.randint(1, 8), "bed_capacity": r.randint(15, 60)}
            for i, (s, n) in enumerate(SPECIALTIES, 1)
        ]
        dept_ids = [x["department_id"] for x in t["departments"]]
        dept_spec = {x["department_id"]: x["specialty_id"] for x in t["departments"]}

        staff, doctors, nurses = [], [], []
        sid = 0

        def add_staff(role: str, dept: str, base: int) -> str:
            nonlocal sid
            sid += 1
            f, l = self.name()
            st_id = f"ST{sid:05d}"
            staff.append({"staff_id": st_id, "first_name": f, "last_name": l, "role": role,
                          "department_id": dept, "hire_date": self.d(date(2010, 1, 1), date(2024, 6, 1)).isoformat(),
                          "employment_status": r.choices(["ACTIVE", "ON_LEAVE", "TERMINATED"], [92, 5, 3])[0],
                          "email": f"{f.lower()}.{st_id.lower()}@mediverse.example",
                          "base_salary": base + r.randint(-5000, 15000)})
            return st_id

        for dept in dept_ids:
            for _ in range(r.randint(5, 9)):
                s = add_staff("DOCTOR", dept, 180000)
                doctors.append({"doctor_id": f"DR{len(doctors) + 1:04d}", "staff_id": s,
                                "specialty_id": dept_spec[dept], "department_id": dept,
                                "license_no": f"LIC-SYN-{r.randint(100000, 999999)}",
                                "is_surgeon": int(dept_spec[dept] in {"SP16", "SP04", "SP01", "SP13", "SP12", "SP03", "SP15", "SP14"} and r.random() < 0.6)})
            for _ in range(r.randint(10, 18)):
                s = add_staff("NURSE", dept, 70000)
                nurses.append({"nurse_id": f"NR{len(nurses) + 1:04d}", "staff_id": s, "department_id": dept,
                               "grade": r.choice(["RN", "Senior RN", "Charge Nurse", "Nurse Practitioner"])})
        for role, n, base in [("LAB_TECH", 40, 55000), ("RADIOLOGY_TECH", 25, 60000),
                              ("PHARMACIST", 15, 95000), ("FINANCE", 10, 75000),
                              ("ADMIN", 12, 50000), ("MANAGEMENT", 6, 160000)]:
            for _ in range(n):
                add_staff(role, r.choice(dept_ids), base)
        t["staff"], t["doctors"], t["nurses"] = staff, doctors, nurses

        # shifts and payroll -------------------------------------------------
        shifts = []
        for s in staff:
            if s["role"] in ("DOCTOR", "NURSE", "LAB_TECH", "RADIOLOGY_TECH", "PHARMACIST") and r.random() < 0.5:
                for k in range(8):
                    day = date(2025, 11, 1) + timedelta(days=k * 3 + r.randint(0, 2))
                    kind = r.choice(["DAY", "EVENING", "NIGHT"])
                    start_h = {"DAY": 7, "EVENING": 15, "NIGHT": 23}[kind]
                    st = datetime(day.year, day.month, day.day, start_h % 24)
                    shifts.append({"shift_id": f"SH{len(shifts) + 1:06d}", "staff_id": s["staff_id"],
                                   "department_id": s["department_id"], "shift_type": kind,
                                   "start_ts": st.isoformat(sep=" "), "end_ts": (st + timedelta(hours=8)).isoformat(sep=" "),
                                   "status": r.choices(["SCHEDULED", "COMPLETED", "SWAPPED", "MISSED"], [40, 50, 7, 3])[0]})
        t["shifts"] = shifts
        payroll = []
        for s in staff:
            if s["employment_status"] == "TERMINATED":
                continue
            for m in range(1, 13):
                gross = round(s["base_salary"] / 12 * r.uniform(0.98, 1.12), 2)
                payroll.append({"payroll_id": f"PY{len(payroll) + 1:07d}", "staff_id": s["staff_id"],
                                "pay_period": f"2025-{m:02d}", "gross_pay": gross,
                                "overtime_pay": round(r.choice([0, 0, 0, r.uniform(100, 2500)]), 2),
                                "deductions": round(gross * r.uniform(0.18, 0.30), 2)})
        t["payroll"] = payroll

        # patients ------------------------------------------------------------
        patients = []
        for i in range(1, self.n_patients + 1):
            f, l = self.name()
            dob = self.d(date(1935, 1, 1), date(2024, 1, 1))
            p = {"patient_id": f"SYN{i:06d}", "first_name": f, "last_name": l, "date_of_birth": dob.isoformat(),
                 "sex": r.choice(["F", "M", "X"]), "blood_type": r.choice(BLOOD), "city": r.choice(CITIES),
                 "phone": self.phone(), "email": f"patient{i}@synthetic.example",
                 "primary_specialty_id": SPECIALTIES[(i - 1) % 20][0],
                 "registered_on": self.d(date(2020, 1, 1), date(2025, 6, 1)).isoformat()}
            if self.defect():
                p[r.choice(["email", "date_of_birth", "phone"])] = r.choice(["not-an-email", "2099-13-45", ""])
            patients.append(p)
        t["patients"] = patients
        pids = [p["patient_id"] for p in patients]
        # duplicate id defect
        for _ in range(max(1, int(self.n_patients * self.defect_rate / 4))):
            patients.append(dict(r.choice(patients[: self.n_patients])))

        t["allergies"] = [{"allergy_id": f"AL{k:06d}", "patient_id": pid, "allergen": r.choice(ALLERGENS),
                           "severity": r.choice(["MILD", "MODERATE", "SEVERE"])}
                          for k, pid in enumerate(r.sample(pids, len(pids) // 4), 1)]

        docs_by_spec: dict[str, list[dict]] = {}
        for d_ in doctors:
            docs_by_spec.setdefault(d_["specialty_id"], []).append(d_)
        dept_by_spec = {v: k for k, v in dept_spec.items()}

        encounters, diagnoses, discharges, prescriptions, med_orders = [], [], [], [], []
        lab_orders, specimens, lab_results, pathology = [], [], [], []
        imaging, rad_reports, surgeries, outcomes, deaths, billing = [], [], [], [], [], []
        drug_ids = [f"DG{i:04d}" for i in range(1, len(DRUG_NAMES) * 3 + 1)]
        dead: set[str] = set()

        for p in patients[: self.n_patients]:
            spec = p["primary_specialty_id"]
            for _ in range(r.choices([1, 2, 3, 4, 5], [30, 30, 20, 12, 8])[0]):
                if p["patient_id"] in dead:
                    break
                doc = r.choice(docs_by_spec[spec])
                etype = r.choices(["OUTPATIENT", "INPATIENT", "EMERGENCY"], [55, 30, 15])[0]
                adm = self.d()
                los = r.randint(1, 14) if etype == "INPATIENT" else 0
                dis = adm + timedelta(days=los)
                eid = f"EN{len(encounters) + 1:07d}"
                enc = {"encounter_id": eid, "patient_id": p["patient_id"], "doctor_id": doc["doctor_id"],
                       "department_id": dept_by_spec[spec], "encounter_type": etype,
                       "admit_date": adm.isoformat(), "discharge_date": dis.isoformat(),
                       "status": "CLOSED" if dis < END else "OPEN"}
                if self.defect():
                    kind = r.choice(["date", "orphan"])
                    if kind == "date":
                        enc["discharge_date"] = (adm - timedelta(days=r.randint(1, 5))).isoformat()
                    else:
                        enc["patient_id"] = "SYN999999"
                encounters.append(enc)
                code, desc = r.choice(DIAG[spec])
                diagnoses.append({"diagnosis_id": f"DX{len(diagnoses) + 1:07d}", "encounter_id": eid,
                                  "icd10_code": code, "description": desc, "is_primary": 1,
                                  "is_sensitive": int(spec == "SP06" or code == "B20")})
                for _ in range(r.randint(0, 2)):
                    dg = r.choice(drug_ids)
                    rx = f"RX{len(prescriptions) + 1:07d}"
                    prescriptions.append({"prescription_id": rx, "encounter_id": eid, "patient_id": p["patient_id"],
                                          "doctor_id": doc["doctor_id"], "drug_id": dg,
                                          "dose": f"{r.choice([5, 10, 20, 50, 100, 250, 500])} mg",
                                          "frequency": r.choice(["OD", "BD", "TDS", "QID", "PRN"]),
                                          "days": r.randint(3, 30), "prescribed_on": adm.isoformat()})
                    if etype == "INPATIENT":
                        med_orders.append({"order_id": f"MO{len(med_orders) + 1:07d}", "prescription_id": rx,
                                           "nurse_id": r.choice(nurses)["nurse_id"],
                                           "administered_ts": datetime(adm.year, adm.month, adm.day, r.randint(0, 23)).isoformat(sep=" "),
                                           "status": r.choice(["GIVEN", "GIVEN", "HELD", "REFUSED"])})
                # labs
                for _ in range(r.choices([0, 1, 2], [35, 45, 20])[0]):
                    lo = f"LO{len(lab_orders) + 1:07d}"
                    tests = r.sample(LAB_TESTS, r.randint(2, 5))
                    collected = datetime(adm.year, adm.month, adm.day, r.randint(6, 20), r.randint(0, 59))
                    lab_orders.append({"lab_order_id": lo, "encounter_id": eid, "patient_id": p["patient_id"],
                                       "ordering_doctor_id": doc["doctor_id"], "ordered_ts": collected.isoformat(sep=" "),
                                       "priority": r.choice(["ROUTINE", "ROUTINE", "URGENT", "STAT"]),
                                       "status": r.choices(["COMPLETED", "IN_PROGRESS", "CANCELLED"], [85, 10, 5])[0]})
                    sp = f"SPC{len(specimens) + 1:07d}"
                    specimens.append({"specimen_id": sp, "lab_order_id": lo, "specimen_type": tests[0][5],
                                      "collected_ts": collected.isoformat(sep=" "),
                                      "received_ts": (collected + timedelta(minutes=r.randint(10, 240))).isoformat(sep=" "),
                                      "condition": r.choices(["ACCEPTABLE", "HEMOLYZED", "INSUFFICIENT"], [92, 5, 3])[0]})
                    for code_, _n, _u, lo_v, hi_v, _s in tests:
                        span = hi_v - lo_v
                        val = round(max(0.0, r.uniform(lo_v - span * 0.3, hi_v + span * 0.4)), 2)
                        if self.defect():
                            val = r.choice([-999.0, 99999.0])
                        flag = "L" if val < lo_v else "H" if val > hi_v else "N"
                        lab_results.append({"result_id": f"LR{len(lab_results) + 1:08d}", "lab_order_id": lo,
                                            "specimen_id": sp, "test_code": code_, "value": val, "flag": flag,
                                            "resulted_ts": (collected + timedelta(hours=r.randint(1, 30))).isoformat(sep=" "),
                                            "verified_by_staff_id": r.choice([s["staff_id"] for s in staff if s["role"] == "LAB_TECH"])})
                    if spec in ("SP03", "SP07", "SP08") and r.random() < 0.4:
                        pathology.append({"pathology_id": f"PA{len(pathology) + 1:06d}", "lab_order_id": lo,
                                          "specimen_site": r.choice(["Skin", "Colon", "Breast", "Lymph node"]),
                                          "report_text": f"SYNTHETIC pathology report. Specimen shows {r.choice(['benign changes', 'chronic inflammation', 'atypical cells requiring correlation', 'no malignancy identified'])}. Margins {r.choice(['clear', 'involved', 'not assessed'])}. For demonstration only.",
                                          "status": r.choice(["FINAL", "FINAL", "PRELIMINARY"])})
                # imaging
                if r.random() < 0.3:
                    mod, regions = r.choice(MODALITIES)
                    st_id = f"IM{len(imaging) + 1:07d}"
                    imaging.append({"study_id": st_id, "encounter_id": eid, "patient_id": p["patient_id"],
                                    "modality": mod, "body_region": r.choice(regions), "study_date": adm.isoformat(),
                                    "ordering_doctor_id": doc["doctor_id"],
                                    "status": r.choices(["REPORTED", "PENDING", "IN_REVIEW"], [80, 12, 8])[0]})
                    rad_reports.append({"report_id": f"RR{len(rad_reports) + 1:07d}", "study_id": st_id,
                                        "radiologist_doctor_id": r.choice(doctors)["doctor_id"],
                                        "findings": f"SYNTHETIC {mod} report: {r.choice(['no acute abnormality', 'mild degenerative change', 'small effusion noted', 'findings suggest follow-up imaging'])}.",
                                        "impression": r.choice(["Normal study", "Abnormal - follow up", "Indeterminate"]),
                                        "report_status": r.choice(["FINAL", "FINAL", "DRAFT"])})
                # surgery
                procs = [x for x in PROCEDURES if x[1] == spec]
                if procs and etype == "INPATIENT" and r.random() < 0.5:
                    surgeons = [d_ for d_ in docs_by_spec[spec] if d_["is_surgeon"]] or docs_by_spec[spec]
                    sg = f"SG{len(surgeries) + 1:06d}"
                    dur = r.randint(30, 360)
                    surgeries.append({"surgery_id": sg, "encounter_id": eid, "patient_id": p["patient_id"],
                                      "surgeon_doctor_id": r.choice(surgeons)["doctor_id"],
                                      "procedure_name": r.choice(procs)[0], "surgery_date": adm.isoformat(),
                                      "anesthesia_type": r.choice(["GENERAL", "SPINAL", "LOCAL", "REGIONAL"]),
                                      "duration_min": dur, "cost": round(dur * r.uniform(40, 120) + 2000, 2)})
                    outcomes.append({"outcome_id": f"SO{len(outcomes) + 1:06d}", "surgery_id": sg,
                                     "outcome": r.choices(["SUCCESSFUL", "COMPLICATION", "READMITTED", "DECEASED"], [86, 9, 4, 1])[0],
                                     "complication_notes": r.choice(["", "", "Minor wound infection (synthetic)", "Post-op nausea (synthetic)"]),
                                     "los_post_op_days": r.randint(0, 10)})
                    if outcomes[-1]["outcome"] == "DECEASED":
                        dead.add(p["patient_id"])
                if etype == "INPATIENT":
                    discharges.append({"discharge_id": f"DS{len(discharges) + 1:07d}", "encounter_id": eid,
                                       "summary_text": (f"SYNTHETIC DISCHARGE SUMMARY. Patient {p['patient_id']} admitted on {adm} "
                                                        f"under {dict(SPECIALTIES)[spec]} with {desc}. Hospital course: "
                                                        f"{r.choice(['uneventful', 'complicated by transient fever', 'responded well to therapy', 'required IV fluids and monitoring'])}. "
                                                        f"Length of stay {los} days. Discharged {r.choice(['home', 'home with follow-up', 'to rehabilitation'])}. "
                                                        f"Follow up in {r.choice([1, 2, 4, 6])} weeks. Demonstration text only - not clinical advice."),
                                       "discharge_disposition": r.choice(["HOME", "HOME", "REHAB", "TRANSFER"]),
                                       "written_by_doctor_id": doc["doctor_id"]})
                billing.append({"bill_id": f"BL{len(billing) + 1:07d}", "encounter_id": eid, "patient_id": p["patient_id"],
                                "amount": round(r.uniform(150, 2000) + los * r.uniform(800, 1500), 2),
                                "payer": r.choice(["SELF", "INSURANCE", "GOVERNMENT"]),
                                "bill_status": r.choices(["PAID", "PENDING", "DENIED"], [70, 25, 5])[0],
                                "billed_on": dis.isoformat()})
            if p["patient_id"] not in dead and r.random() < 0.012:
                dead.add(p["patient_id"])
            if p["patient_id"] in dead:
                last = max((e["discharge_date"] for e in encounters if e["patient_id"] == p["patient_id"]), default=END.isoformat())
                deaths.append({"death_id": f"DT{len(deaths) + 1:05d}", "patient_id": p["patient_id"], "date_of_death": last,
                               "cause_icd10": r.choice(["I21.9", "C34.90", "A41.9", "J44.1"]),
                               "location": r.choice(["ICU", "WARD", "ED", "OR"])})

        t.update(encounters=encounters, diagnoses=diagnoses, discharge_summaries=discharges,
                 prescriptions=prescriptions, medication_orders=med_orders, lab_orders=lab_orders,
                 specimens=specimens, lab_results=lab_results, pathology_reports=pathology,
                 imaging_studies=imaging, radiology_reports=rad_reports, surgeries=surgeries,
                 surgery_outcomes=outcomes, death_records=deaths, billing=billing)
        t["lab_test_catalog"] = [{"test_code": c, "test_name": n, "unit": u, "ref_low": lo, "ref_high": hi, "specimen_type": s}
                                 for c, n, u, lo, hi, s in LAB_TESTS]

        # pharmacy -------------------------------------------------------------
        t["manufacturers"] = [{"manufacturer_id": f"MF{i:03d}", "name": f"Synthetic Pharma {c}", "country": r.choice(["IN", "US", "DE", "CH", "JP"])}
                              for i, c in enumerate("ABCDEFGHIJ", 1)]
        t["suppliers"] = [{"supplier_id": f"SU{i:03d}", "name": f"Demo Med Supply {i}", "contact_email": f"orders{i}@supplier.example",
                           "lead_time_days": r.randint(2, 21), "rating": round(r.uniform(2.5, 5), 1)} for i in range(1, 13)]
        drugs = []
        for i, did in enumerate(drug_ids):
            base = DRUG_NAMES[i % len(DRUG_NAMES)]
            drugs.append({"drug_id": did, "drug_name": f"{base} {r.choice(['250mg', '500mg', '10mg', '20mg', '5ml'])}",
                          "generic_name": base, "form": r.choice(["TABLET", "CAPSULE", "INJECTION", "SYRUP", "INHALER"]),
                          "manufacturer_id": r.choice(t["manufacturers"])["manufacturer_id"],
                          "unit_price": round(r.uniform(0.1, 45), 2), "reorder_level": r.choice([100, 200, 500]),
                          "is_controlled": int(base in ("Morphine",))})
        t["drugs"] = drugs
        batches, purchases = [], []
        for dg in drugs:
            for _ in range(r.randint(1, 4)):
                b = f"BT{len(batches) + 1:06d}"
                recv = self.d(date(2024, 1, 1), date(2025, 11, 1))
                qty = r.randint(50, 2000)
                batches.append({"batch_id": b, "drug_id": dg["drug_id"], "supplier_id": r.choice(t["suppliers"])["supplier_id"],
                                "lot_no": f"LOT{r.randint(10000, 99999)}", "received_date": recv.isoformat(),
                                "expiry_date": (recv + timedelta(days=r.randint(60, 900))).isoformat(),
                                "qty_received": qty, "qty_on_hand": r.randint(0, qty)})
                purchases.append({"purchase_id": f"PO{len(purchases) + 1:06d}", "batch_id": b, "supplier_id": batches[-1]["supplier_id"],
                                  "order_date": (recv - timedelta(days=r.randint(2, 20))).isoformat(),
                                  "qty": qty, "total_cost": round(qty * dg["unit_price"] * r.uniform(0.6, 0.9), 2)})
        t["stock_batches"], t["purchases"] = batches, purchases
        pharm = [s["staff_id"] for s in staff if s["role"] == "PHARMACIST"]
        bdrug: dict[str, list[str]] = {}
        for b in batches:
            bdrug.setdefault(b["drug_id"], []).append(b["batch_id"])
        t["dispensing"] = [{"dispense_id": f"DP{k:07d}", "prescription_id": rx["prescription_id"],
                            "batch_id": r.choice(bdrug[rx["drug_id"]]), "pharmacist_staff_id": r.choice(pharm),
                            "qty": r.randint(5, 60), "dispensed_on": rx["prescribed_on"]}
                           for k, rx in enumerate(prescriptions, 1) if r.random() < 0.8]

        # operations / finance ------------------------------------------------
        equipment = [{"equipment_id": f"EQ{i:04d}", "name": r.choice(EQUIPMENT), "department_id": r.choice(dept_ids),
                      "purchase_date": self.d(date(2015, 1, 1), date(2025, 1, 1)).isoformat(),
                      "purchase_cost": round(r.uniform(2000, 2500000), 2),
                      "status": r.choices(["IN_SERVICE", "UNDER_MAINTENANCE", "RETIRED"], [85, 10, 5])[0]} for i in range(1, 181)]
        t["equipment"] = equipment
        t["maintenance"] = [{"maintenance_id": f"MT{k:05d}", "equipment_id": e["equipment_id"], "service_date": self.d().isoformat(),
                             "maintenance_type": r.choice(["PREVENTIVE", "CORRECTIVE", "CALIBRATION"]),
                             "cost": round(r.uniform(100, 25000), 2), "vendor": f"Demo Service Co {r.randint(1, 6)}"}
                            for k, e in enumerate([e for e in equipment for _ in range(r.randint(0, 4))], 1)]
        t["dept_budgets"] = [{"budget_id": f"BG{d_}{y}", "department_id": d_, "fiscal_year": y,
                              "budget_amount": round(r.uniform(1.5e6, 9e6), 2)} for d_ in dept_ids for y in (2024, 2025)]
        cats = ["SUPPLIES", "UTILITIES", "EQUIPMENT", "MAINTENANCE", "SERVICES", "TRAINING"]
        t["expenses"] = [{"expense_id": f"EX{k:06d}", "department_id": r.choice(dept_ids), "category": r.choice(cats),
                          "expense_date": self.d(date(2024, 1, 1)).isoformat(), "amount": round(r.uniform(200, 90000), 2),
                          "description": f"Synthetic {r.choice(cats).lower()} expense"} for k in range(1, 6001)]
        return t


def write(tables: dict[str, list[dict]], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        with (out / f"{name}.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "data" / "synthetic"))
    ap.add_argument("--seed", type=int, default=360)
    ap.add_argument("--patients", type=int, default=4000)
    ap.add_argument("--defect-rate", type=float, default=0.02)
    a = ap.parse_args()
    tables = Gen(a.seed, a.patients, a.defect_rate).run()
    write(tables, Path(a.out))
    for k, v in sorted(tables.items()):
        print(f"{k:22s} {len(v):>8d}")


if __name__ == "__main__":
    main()
