r"""NCAA team identity for the Kalshi RFQ fair book: Kalshi code tables and the BettingPros crosswalk.

BUILT FROM LIVE DATA on 2026-11-10 (scripts/probe in the PR packet): the Kalshi code universe and each code's school name are
Kalshi's own `yes_sub_title` text on open KXNCAAF*/KXNCAAMB* GAME markets; the BettingPros side is the code + participant city
BettingPros' /v3 offers carry. A BP code maps to a Kalshi code ONLY where the two sources' school names agree after
normalisation (plus documented aliases), or where both sources list the same game on the same date with the other team
already mapped. Pairs that satisfy neither rule are NOT in these tables: a game touching one fails closed (never priced)
rather than being guessed. Subtitle census counts: NCAAF 266 codes, NCAAB 301 codes (299 in the first census plus AKR and
UNCG, found on open markets by the completion census the same day; both carry verbatim Kalshi names below and AKR is
already a crosswalk target, UNCG is not yet a BP-mapped code). BettingPros codes left unmapped
(FBS/D1 opponents with no Kalshi market that week, D2/D3 schools): NCAAF 1 of 259 named, NCAAB 46 of 345 named.
Refresh: re-run the census + join on a slate week; a code Kalshi adds later fails closed until the table is rebuilt.

SPLIT SAFETY (added 2026-11-10 after the live-event audit): Kalshi concatenates the two team codes into one
event-ticker blob with NO separator, and the strict mapper resolves a blob only when exactly one split into two
census codes exists. The full census is NOT pairwise split-unique: measured on these tables there are exactly 5
ambiguous blobs per sport, each with exactly two splits (NCAAF, 4 distinct blobs: HCUND, MEMORE, MEMOSU, WMUND,
from the prefix collisions HC/HCU, ME/MEM, WM/WMU around ND/UND, ORE, OSU; NCAAB, 10 distinct blobs: HCUCSB,
HCUSF, MASSLBSU, MASSLMD, NEVILL, SFAMER, SIUEVAN, UNCAMER, WMUCSB, WMUSF). Every code involved is live-used,
so no code can be dropped from the census without losing a real school (Notre Dame and North Carolina among
them). The adapters therefore parse with `parse_codes`: the census MINUS the PARSE_DROPPED_CODES below, the
minimum drop set that makes every remaining ordered pair split uniquely (proof: the parametrized uniqueness
test runs over parse_codes). Each dropped code keeps its census entry, its verbatim name (so the fair book's
name route still resolves the school), and its crosswalk entries; only ticker-blob parsing excludes it, so a
blob whose only split needs a dropped code fails closed as ambiguous instead of being guessed. Measured cost on
the 2026-11-10 live slate: NCAAF drops {ME, ND} and 4 of 258 open GAME events fail closed (BCND, MEALBY, SMUND,
UNHME); NCAAB drops {AMER, CSB, EVAN, ILL, MASS, SF} and 7 of 185 fail closed. Every other live event blob
across GAME/SPREAD/TOTAL resolved to exactly one split under the full census (NCAAF 258/258 after completion,
NCAAB 185/185), which is the empirical basis for trusting unique splits at all.
"""

from __future__ import annotations



# ---- NCAAF: Kalshi codes observed on open KXNCAAFGAME markets (2026-11-10) ----
NCAAF_KALSHI_CODES = frozenset("""
AAMU AC AFA AKR ALA ALBY ALCN ALST APP ARIZ
ARK ARMY ARPB ARST ASU AUB BALL BAY BC BGSU
BRWN BRY BSU BUCK BUFF BUT BYU CAL CAMP CARK
CCAR CCSU CHAR CHAT CHS CHSO CIN CIT CLEM CLMB
CMU COLG COLO CONN COOK COR CP CSU DART DAV
DAY DEL DRKE DSU DUKE DUQ ECU EIU EKY ELON
EMU ETAM ETSU EWU FAMU FAU FIU FLA FOR FRES
FSU FUR GASO GAST GRAM GT GTWN HAMP HARV HAW
HC HCU HOU HOW IDHO IDST ILL ILST IND INST
IOWA ISU IW JKST JMU JVST KENN KENT KSU KU
LAF LAM LEH LIB LINW LIU LOU LSU LT MASS
MCNS MD ME MEM MER MHU MIA MICH MINN MISS
MIZZ MOH MONM MONT MORE MORG MOSU MRMK MRSH MRST
MSST MSU MTST MTU MURR MVSU NAU NAVY NCAT NCCU
NCST ND NDSU NEB NEV NHC NICH NIU NMSU NORF
NW NWST ODU OHIO OKLA OKST ORE ORST OSU PEAY
PENN PITT PRE PRIN PRST PSU PUR PV RICE RICH
RMU RUTG SAC SAM SCAR SCST SDAK SDST SDSU SELA
SEMO SFA SHSU SHU SIU SJSU SMU SOU STAN STET
STNH STON SUU SYR TARL TCU TEM TENN TEX TLSA
TNST TNTC TOL TOWS TROY TTU TULN TXAM TXSO TXST
UAB UCD UCF UCLA UGA UK ULL ULM UNA UNC
UNCO UND UNH UNI UNLV UNM UNT URI USA USC
USD USF USM UST USU UTAH UTEP UTM UTRGV UTSA
UTU UVA UWFL UWGA VALP VAN VILL VMI VT WAG
WAKE WASH WCU WEB WEBB WIS WIU WKU WM WMU
WOF WSU WVU WYO YALE YSU
""".split())

# Kalshi code -> school name, verbatim from Kalshi yes_sub_title (identity evidence; also the name lookup for
# feeds that name teams, e.g. a ParlayAPI slate: a name that is not here verbatim does not resolve).
NCAAF_KALSHI_NAMES = {
    "AAMU": "Alabama A&M",  "AC": "Abilene Christian",  "AFA": "Air Force",  "AKR": "Akron",
    "ALA": "Alabama",  "ALBY": "University at Albany",  "ALCN": "Alcorn St.",  "ALST": "Alabama St.",
    "APP": "Appalachian St.",  "ARIZ": "Arizona",  "ARK": "Arkansas",  "ARMY": "Army",
    "ARPB": "Arkansas-Pine Bluff",  "ARST": "Arkansas St.",  "ASU": "Arizona St.",  "AUB": "Auburn",
    "BALL": "Ball St.",  "BAY": "Baylor",  "BC": "Boston College",  "BGSU": "Bowling Green",
    "BRWN": "Brown",  "BRY": "Bryant",  "BSU": "Boise St.",  "BUCK": "Bucknell",
    "BUFF": "Buffalo",  "BUT": "Butler",  "BYU": "BYU",  "CAL": "California",
    "CAMP": "Campbell",  "CARK": "Central Arkansas",  "CCAR": "Coastal Carolina",  "CCSU": "Central Connecticut St.",
    "CHAR": "Charlotte",  "CHAT": "Chattanooga",  "CHS": "Chicago St.",  "CHSO": "Charleston Southern",
    "CIN": "Cincinnati",  "CIT": "The Citadel",  "CLEM": "Clemson",  "CLMB": "Columbia",
    "CMU": "Central Michigan",  "COLG": "Colgate",  "COLO": "Colorado",  "CONN": "UConn",
    "COOK": "Bethune-Cookman",  "COR": "Cornell",  "CP": "Cal Poly",  "CSU": "Colorado St.",
    "DART": "Dartmouth",  "DAV": "Davidson",  "DAY": "Dayton",  "DEL": "Delaware",
    "DRKE": "Drake",  "DSU": "Delaware St.",  "DUKE": "Duke",  "DUQ": "Duquesne",
    "ECU": "East Carolina",  "EIU": "Eastern Illinois",  "EKY": "Eastern Kentucky",  "ELON": "Elon",
    "EMU": "Eastern Michigan",  "ETAM": "East Texas A&M",  "ETSU": "East Tennessee St.",  "EWU": "Eastern Washington",
    "FAMU": "Florida A&M",  "FAU": "Florida Atlantic",  "FIU": "Florida International",  "FLA": "Florida",
    "FOR": "Fordham",  "FRES": "Fresno St.",  "FSU": "Florida St.",  "FUR": "Furman",
    "GASO": "Georgia Southern",  "GAST": "Georgia St.",  "GRAM": "Grambling St.",  "GT": "Georgia Tech",
    "GTWN": "Georgetown",  "HAMP": "Hampton",  "HARV": "Harvard",  "HAW": "Hawai'i",
    "HC": "Holy Cross",  "HCU": "Houston Christian",  "HOU": "Houston",  "HOW": "Howard",
    "IDHO": "Idaho",  "IDST": "Idaho St.",  "ILL": "Illinois",  "ILST": "Illinois St.",
    "IND": "Indiana",  "INST": "Indiana St.",  "IOWA": "Iowa",  "ISU": "Iowa St.",
    "IW": "Incarnate Word",  "JKST": "Jackson St.",  "JMU": "James Madison",  "JVST": "Jacksonville St.",
    "KENN": "Kennesaw St.",  "KENT": "Kent St.",  "KSU": "Kansas St.",  "KU": "Kansas",
    "LAF": "Lafayette",  "LAM": "Lamar",  "LEH": "Lehigh",  "LIB": "Liberty",
    "LINW": "Lindenwood",  "LIU": "LIU",  "LOU": "Louisville",  "LSU": "LSU",
    "LT": "Louisiana Tech",  "MASS": "UMass",  "MCNS": "McNeese",  "MD": "Maryland",
    "ME": "Maine",  "MEM": "Memphis",  "MER": "Mercer",  "MHU": "Mercyhurst",
    "MIA": "Miami (FL)",  "MICH": "Michigan",  "MINN": "Minnesota",  "MISS": "Ole Miss",
    "MIZZ": "Missouri",  "MOH": "Miami (OH)",  "MONM": "Monmouth",  "MONT": "Montana",
    "MORE": "Morehead St.",  "MORG": "Morgan St.",  "MOSU": "Missouri St.",  "MRMK": "Merrimack",
    "MRSH": "Marshall",  "MRST": "Marist",  "MSST": "Mississippi St.",  "MSU": "Michigan St.",
    "MTST": "Montana St.",  "MTU": "Middle Tennessee",  "MURR": "Murray St.",  "MVSU": "Mississippi Valley St.",
    "NAU": "Northern Arizona",  "NAVY": "Navy",  "NCAT": "North Carolina A&T",  "NCCU": "North Carolina Central",
    "NCST": "NC St.",  "ND": "Notre Dame",  "NDSU": "North Dakota St.",  "NEB": "Nebraska",
    "NEV": "Nevada",  "NHC": "New Haven",  "NICH": "Nicholls St.",  "NIU": "Northern Illinois",
    "NMSU": "New Mexico St.",  "NORF": "Norfolk St.",  "NW": "Northwestern",  "NWST": "Northwestern St.",
    "ODU": "Old Dominion",  "OHIO": "Ohio",  "OKLA": "Oklahoma",  "OKST": "Oklahoma St.",
    "ORE": "Oregon",  "ORST": "Oregon St.",  "OSU": "Ohio St.",  "PEAY": "Austin Peay",
    "PENN": "Penn",  "PITT": "Pittsburgh",  "PRE": "Presbyterian",  "PRIN": "Princeton",
    "PRST": "Portland St.",  "PSU": "Penn St.",  "PUR": "Purdue",  "PV": "Prairie View A&M",
    "RICE": "Rice",  "RICH": "Richmond",  "RMU": "Robert Morris",  "RUTG": "Rutgers",
    "SAC": "Sacramento St.",  "SAM": "Samford",  "SCAR": "South Carolina",  "SCST": "South Carolina St.",
    "SDAK": "South Dakota",  "SDST": "South Dakota St.",  "SDSU": "San Diego St.",  "SELA": "Southeastern Louisiana",
    "SEMO": "Southeast Missouri St.",  "SFA": "Stephen F. Austin",  "SHSU": "Sam Houston",  "SHU": "Sacred Heart",
    "SIU": "Southern Illinois",  "SJSU": "San Jose St.",  "SMU": "SMU",  "SOU": "Southern University",
    "STAN": "Stanford",  "STET": "Stetson",  "STNH": "Stonehill",  "STON": "Stony Brook",
    "SUU": "Southern Utah",  "SYR": "Syracuse",  "TARL": "Tarleton St.",  "TCU": "TCU",
    "TEM": "Temple",  "TENN": "Tennessee",  "TEX": "Texas",  "TLSA": "Tulsa",
    "TNST": "Tennessee St.",  "TNTC": "Tennessee Tech",  "TOL": "Toledo",  "TOWS": "Towson",
    "TROY": "Troy",  "TTU": "Texas Tech",  "TULN": "Tulane",  "TXAM": "Texas A&M",
    "TXSO": "Texas Southern",  "TXST": "Texas St.",  "UAB": "UAB",  "UCD": "UC Davis",
    "UCF": "UCF",  "UCLA": "UCLA",  "UGA": "Georgia",  "UK": "Kentucky",
    "ULL": "Louisiana",  "ULM": "Louisiana-Monroe",  "UNA": "North Alabama",  "UNC": "North Carolina",
    "UNCO": "Northern Colorado",  "UND": "North Dakota",  "UNH": "New Hampshire",  "UNI": "Northern Iowa",
    "UNLV": "UNLV",  "UNM": "New Mexico",  "UNT": "North Texas",  "URI": "Rhode Island",
    "USA": "South Alabama",  "USC": "USC",  "USD": "San Diego",  "USF": "South Florida",
    "USM": "Southern Miss",  "UST": "St. Thomas",  "USU": "Utah St.",  "UTAH": "Utah",
    "UTEP": "UTEP",  "UTM": "Tennessee-Martin",  "UTRGV": "UT Rio Grande Valley",  "UTSA": "UTSA",
    "UTU": "Utah Tech",  "UVA": "Virginia",  "UWFL": "West Florida",  "UWGA": "West Georgia",
    "VALP": "Valparaiso",  "VAN": "Vanderbilt",  "VILL": "Villanova",  "VMI": "VMI",
    "VT": "Virginia Tech",  "WAG": "Wagner",  "WAKE": "Wake Forest",  "WASH": "Washington",
    "WCU": "Western Carolina",  "WEB": "Weber St.",  "WEBB": "Gardner-Webb",  "WIS": "Wisconsin",
    "WIU": "Western Illinois",  "WKU": "Western Kentucky",  "WM": "William & Mary",  "WMU": "Western Michigan",
    "WOF": "Wofford",  "WSU": "Washington St.",  "WVU": "West Virginia",  "WYO": "Wyoming",
    "YALE": "Yale",  "YSU": "Youngstown St.",
}

# BettingPros team code -> Kalshi code (name-joined; see module docstring for the join rule).
NCAAF_BP_TO_KALSHI = {
    "AAM": "AAMU",  "ACU": "AC",  "AF": "AFA",  "AKR": "AKR",  "ALAST": "ALST",  "ALB": "ALBY",
    "ALCST": "ALCN",  "APB": "ARPB",  "APP": "APP",  "APY": "PEAY",  "ARI": "ARIZ",  "ARK": "ARK",
    "ARKS": "ARST",  "ASU": "ASU",  "AUB": "AUB",  "BALL": "BALL",  "BAMA": "ALA",  "BAY": "BAY",
    "BC": "BC",  "BCU": "COOK",  "BGN": "BGSU",  "BOISE": "BSU",  "BRN": "BRWN",  "BRY": "BRY",
    "BUC": "BUCK",  "BUF": "BUFF",  "BUT": "BUT",  "BYU": "BYU",  "CAL": "CAL",  "CAM": "CAMP",
    "CC": "CCAR",  "CCH": "CHSO",  "CCSU": "CCSU",  "CGT": "COLG",  "CHA": "CHAR",  "CHS": "CHS",
    "CHT": "CHAT",  "CIN": "CIN",  "CIT": "CIT",  "CLE": "CLEM",  "CMB": "CLMB",  "CMC": "CMU",
    "COL": "COLO",  "COR": "COR",  "CSU": "CSU",  "CSUS": "SAC",  "DAV": "DAV",  "DAY": "DAY",
    "DEL": "DEL",  "DLS": "DSU",  "DRA": "DRKE",  "DRT": "DART",  "DUK": "DUKE",  "DUQ": "DUQ",
    "ECU": "ECU",  "EIL": "EIU",  "EKY": "EKY",  "ELO": "ELON",  "ETSU": "ETSU",  "EW": "EWU",
    "FAMU": "FAMU",  "FAU": "FAU",  "FIU": "FIU",  "FLA": "FLA",  "FOR": "FOR",  "FRE": "FRES",
    "FSU": "FSU",  "FUR": "FUR",  "GRA": "GRAM",  "GSO": "GASO",  "GST": "GAST",  "GT": "GT",
    "GTOWN": "GTWN",  "GWB": "WEBB",  "HAMP": "HAMP",  "HAR": "HARV",  "HAW": "HAW",  "HB": "HCU",
    "HC": "HC",  "HOU": "HOU",  "HOW": "HOW",  "IDA": "IDHO",  "IDS": "IDST",  "ILL": "ILL",
    "ILS": "ILST",  "INDS": "INST",  "IOW": "IOWA",  "ISU": "ISU",  "IU": "IND",  "IW": "IW",
    "JM": "JMU",  "JST": "JKST",  "JVS": "JVST",  "KAN": "KU",  "KEN": "UK",  "KENN": "KENN",
    "KNT": "KENT",  "KST": "KSU",  "LAF": "LAF",  "LAM": "LAM",  "LEI": "LEH",  "LIB": "LIB",
    "LINU": "LINW",  "LIUCWP": "LIU",  "LOU": "LOU",  "LSU": "LSU",  "LT": "LT",  "MAR": "MD",
    "MC": "MRMK",  "MCN": "MCNS",  "MEM": "MEM",  "MER": "MER",  "MFL": "MIA",  "MGN": "MORG",
    "MHU": "MHU",  "MICH": "MICH",  "MIN": "MINN",  "MIS": "MISS",  "MIZ": "MIZZ",  "MIZST": "MOSU",
    "MNM": "MONM",  "MOH": "MOH",  "MONT": "MONT",  "MOR": "MORE",  "MOS": "MTST",  "MSH": "MRSH",
    "MSST": "MSST",  "MST": "MRST",  "MSU": "MSU",  "MTS": "MTU",  "MUR": "MURR",  "MVS": "MVSU",
    "NAT": "NCAT",  "NAZ": "NAU",  "NC": "UNC",  "NCC": "NCCU",  "NCST": "NCST",  "ND": "ND",
    "NDS": "NDSU",  "NEB": "NEB",  "NEV": "NEV",  "NFS": "NORF",  "NH": "UNH",  "NIC": "NICH",
    "NIL": "NIU",  "NM": "UNM",  "NMS": "NMSU",  "NOCO": "UNCO",  "NTX": "UNT",  "NW": "NW",
    "NWS": "NWST",  "OHI": "OHIO",  "OKL": "OKLA",  "OKS": "OKST",  "ORE": "ORE",  "ORS": "ORST",
    "OSU": "OSU",  "PEN": "PENN",  "PIT": "PITT",  "PRES": "PRE",  "PRI": "PRIN",  "PRST": "PRST",
    "PSU": "PSU",  "PUR": "PUR",  "RCH": "RICH",  "RICE": "RICE",  "RIL": "URI",  "RM": "RMU",
    "RUT": "RUTG",  "SAB": "USA",  "SAM": "SAM",  "SAU": "SFA",  "SBK": "STON",  "SC": "SCAR",
    "SCH": "SHU",  "SCS": "SCST",  "SD": "SDAK",  "SDS": "SDST",  "SDSU": "SDSU",  "SEL": "SELA",
    "SEM": "SEMO",  "SHS": "SHSU",  "SIL": "SIU",  "SJS": "SJSU",  "SMU": "SMU",  "STA": "STAN",
    "STHL": "STNH",  "STU": "STET",  "SU": "SOU",  "SUT": "SUU",  "SYR": "SYR",  "TAMC": "ETAM",
    "TCU": "TCU",  "TEM": "TEM",  "TEN": "TENN",  "TEP": "UTEP",  "TEX": "TEX",  "TNM": "UTM",
    "TNST": "TNST",  "TNT": "TNTC",  "TOL": "TOL",  "TRY": "TROY",  "TSA": "TLSA",  "TSO": "TXSO",
    "TSU": "TARL",  "TT": "TTU",  "TUL": "TULN",  "TWN": "TOWS",  "TXAM": "TXAM",  "TXST": "TXST",
    "UAB": "UAB",  "UCA": "CARK",  "UCD": "UCD",  "UCF": "UCF",  "UCLA": "UCLA",  "UCONN": "CONN",
    "UGA": "UGA",  "ULL": "ULL",  "ULM": "ULM",  "UMAINE": "ME",  "UMASS": "MASS",  "UNA": "UNA",
    "UNH": "NHC",  "UNI": "UNI",  "UNLV": "UNLV",  "USC": "USC",  "USF": "USF",  "USM": "USM",
    "UST": "UST",  "UTH": "UTAH",  "UTRGV": "UTRGV",  "UTS": "USU",  "UTSA": "UTSA",  "UTU": "UTU",
    "UVA": "UVA",  "UWF": "UWFL",  "UWGA": "UWGA",  "VAL": "VALP",  "VAN": "VAN",  "VIL": "VILL",
    "VMI": "VMI",  "VT": "VT",  "WAG": "WAG",  "WAS": "WASH",  "WBS": "WEB",  "WC": "WCU",
    "WF": "WAKE",  "WIL": "WIU",  "WIS": "WIS",  "WKY": "WKU",  "WM": "WM",  "WMC": "WMU",
    "WOF": "WOF",  "WST": "WSU",  "WVU": "WVU",  "WYO": "WYO",  "YAL": "YALE",  "YSU": "YSU",
}
# (the duplicate module docstring that used to sit here was a build artifact; the real one is at the top of the file.)


# ---- NCAAB: Kalshi codes observed on open KXNCAAMBGAME markets (2026-11-10) ----
NCAAB_KALSHI_CODES = frozenset("""
AAMU AC AFA ALA ALBY ALCN AMCC AMER APP ARIZ
ARK ARMY ARPB ARST ASU AUB BALL BAY BC BEL
BGSU BRAD BRWN BRY BSU BUCK BUT BYU CAL CAMP
CAN CARK CBU CCSU CHAR CHAT CHS CHSO CLEM CLEV
CLMB CMU COFC COLG COLO CONN COOK COPP COR CP
CREI CSB CSU DART DAV DAY DEL DEP DET DREX
DRKE DSU DUKE DUQ ECU EKY ELON ETAM ETSU EVAN
FAIR FAMU FAU FDU FGCU FIU FLA FOR FRES FSU
FUR GASO GAST GB GC GMU GT GTWN GW HALL
HARV HC HCU HOF HOU HOW HP IDHO ILL ILST
IND INST IONA IOWA ISU IUIN IW JKST JMU JOES
JVST KENN KENT KSU KU LAS LBSU LCHI LEH LIB
LINW LIP LIU LMD LMU MARQ MASS MASSL MD ME
MEM MER MHU MIA MICH MILW MINN MISS MONM MONT
MORG MRMK MRST MSM MSST MSU MTST MTU MURR MVSU
NAU NAVY NCAT NCCU NCST ND NDSU NE NEB NEOM
NEV NHC NIAG NICH NJIT NKU NMSU NORF NW OAK
ODU OHIO OKLA OKST ORE ORST ORU OSU PAC PENN
PEPP PFW PITT PORT PRE PRIN PROV PRST PSU PUR
PV QUC QUIN RICH RMU RUTG SAM SBON SCAR SCST
SCU SDAK SDST SDSU SEA SELA SEMO SF SFA SHSU
SHU SIE SIU SIUE SJSU SJU SLU SMC SMU SOU
SPC STAN STET STON SUU TARL TCU TEM TENN TLSA
TNST TNTC TOL TOWS TROY TTU TULN TXAM TXSO TXST
UALR UCD UCF UCLA UCRV UCSB UCSD UGA UIC UK
ULM UMBC UMES UMKC UNA UNC UNCA UNCG UNCW UNI UNLV
UNM UNT URI USA USC USD USF USI USM USU AKR
UTAH UTEP UTM UTRGV UTSA UTU UVA UWFL UWGA VALP
VAN VCU VILL VMI VT WAKE WEB WIN WIS WIU
WM WMU WRST WSU WVU WYO XAV YALE YSU
""".split())

# Kalshi code -> school name, verbatim from Kalshi yes_sub_title (identity evidence; also the name lookup for
# feeds that name teams, e.g. a ParlayAPI slate: a name that is not here verbatim does not resolve).
NCAAB_KALSHI_NAMES = {
    "AAMU": "Alabama A&M",  "AC": "Abilene Christian",  "AFA": "Air Force",  "ALA": "Alabama",
    "ALBY": "University at Albany",  "ALCN": "Alcorn St.",  "AMCC": "Texas A&M-Corpus Christi",  "AMER": "American",
    "APP": "Appalachian St.",  "ARIZ": "Arizona",  "ARK": "Arkansas",  "ARMY": "Army",
    "ARPB": "Arkansas-Pine Bluff",  "ARST": "Arkansas St.",  "ASU": "Arizona St.",  "AUB": "Auburn",
    "BALL": "Ball St.",  "BAY": "Baylor",  "BC": "Boston College",  "BEL": "Belmont",
    "BGSU": "Bowling Green",  "BRAD": "Bradley",  "BRWN": "Brown",  "BRY": "Bryant",
    "BSU": "Boise St.",  "BUCK": "Bucknell",  "BUT": "Butler",  "BYU": "BYU",
    "CAL": "California",  "CAMP": "Campbell",  "CAN": "Canisius",  "CARK": "Central Arkansas",
    "CBU": "California Baptist",  "CCSU": "Central Connecticut St.",  "CHAR": "Charlotte",  "CHAT": "Chattanooga",
    "CHS": "Chicago St.",  "CHSO": "Charleston Southern",  "CLEM": "Clemson",  "CLEV": "Cleveland St.",
    "CLMB": "Columbia",  "CMU": "Central Michigan",  "COFC": "Charleston",  "COLG": "Colgate",
    "COLO": "Colorado",  "CONN": "UConn",  "COOK": "Bethune-Cookman",  "COPP": "Coppin St.",
    "COR": "Cornell",  "CP": "Cal Poly",  "CREI": "Creighton",  "CSB": "Cal State Bakersfield",
    "CSU": "Colorado St.",  "DART": "Dartmouth",  "DAV": "Davidson",  "DAY": "Dayton",
    "DEL": "Delaware",  "DEP": "DePaul",  "DET": "Detroit Mercy",  "DREX": "Drexel",
    "DRKE": "Drake",  "DSU": "Delaware St.",  "DUKE": "Duke",  "DUQ": "Duquesne",
    "ECU": "East Carolina",  "EKY": "Eastern Kentucky",  "ELON": "Elon",  "ETAM": "East Texas A&M",
    "ETSU": "East Tennessee St.",  "EVAN": "Evansville",  "FAIR": "Fairfield",  "FAMU": "Florida A&M",
    "FAU": "Florida Atlantic",  "FDU": "FDU",  "FGCU": "Florida Gulf Coast",  "FIU": "Florida International",
    "FLA": "Florida",  "FOR": "Fordham",  "FRES": "Fresno St.",  "FSU": "Florida St.",
    "FUR": "Furman",  "GASO": "Georgia Southern",  "GAST": "Georgia St.",  "GB": "Green Bay",
    "GC": "Grand Canyon",  "GMU": "George Mason",  "GT": "Georgia Tech",  "GTWN": "Georgetown",
    "GW": "George Washington",  "HALL": "Seton Hall",  "HARV": "Harvard",  "HC": "Holy Cross",
    "HCU": "Houston Christian",  "HOF": "Hofstra",  "HOU": "Houston",  "HOW": "Howard",
    "HP": "High Point",  "IDHO": "Idaho",  "ILL": "Illinois",  "ILST": "Illinois St.",
    "IND": "Indiana",  "INST": "Indiana St.",  "IONA": "Iona",  "IOWA": "Iowa",
    "ISU": "Iowa St.",  "IUIN": "IU Indy",  "IW": "Incarnate Word",  "JKST": "Jackson St.",
    "JMU": "James Madison",  "JOES": "Saint Joseph's",  "JVST": "Jacksonville St.",  "KENN": "Kennesaw St.",
    "KENT": "Kent St.",  "KSU": "Kansas St.",  "KU": "Kansas",  "LAS": "La Salle",
    "LBSU": "Long Beach St.",  "LCHI": "Loyola Chicago",  "LEH": "Lehigh",  "LIB": "Liberty",
    "LINW": "Lindenwood",  "LIP": "Lipscomb",  "LIU": "LIU",  "LMD": "Loyola Maryland",
    "LMU": "Loyola Marymount",  "MARQ": "Marquette",  "MASS": "UMass",  "MASSL": "UMass Lowell",
    "MD": "Maryland",  "ME": "Maine",  "MEM": "Memphis",  "MER": "Mercer",
    "MHU": "Mercyhurst",  "MIA": "Miami (FL)",  "MICH": "Michigan",  "MILW": "Milwaukee",
    "MINN": "Minnesota",  "MISS": "Ole Miss",  "MONM": "Monmouth",  "MONT": "Montana",
    "MORG": "Morgan St.",  "MRMK": "Merrimack",  "MRST": "Marist",  "MSM": "Mount St. Mary's",
    "MSST": "Mississippi St.",  "MSU": "Michigan St.",  "MTST": "Montana St.",  "MTU": "Middle Tennessee",
    "MURR": "Murray St.",  "MVSU": "Mississippi Valley St.",  "NAU": "Northern Arizona",  "NAVY": "Navy",
    "NCAT": "North Carolina A&T",  "NCCU": "North Carolina Central",  "NCST": "NC St.",  "ND": "Notre Dame",
    "NDSU": "North Dakota St.",  "NE": "Northeastern",  "NEB": "Nebraska",  "NEOM": "Omaha",
    "NEV": "Nevada",  "NHC": "New Haven",  "NIAG": "Niagara",  "NICH": "Nicholls St.",
    "NJIT": "NJIT",  "NKU": "Northern Kentucky",  "NMSU": "New Mexico St.",  "NORF": "Norfolk St.",
    "NW": "Northwestern",  "OAK": "Oakland",  "ODU": "Old Dominion",  "OHIO": "Ohio",
    "OKLA": "Oklahoma",  "OKST": "Oklahoma St.",  "ORE": "Oregon",  "ORST": "Oregon St.",
    "ORU": "Oral Roberts",  "OSU": "Ohio St.",  "PAC": "Pacific",  "PENN": "Penn",
    "PEPP": "Pepperdine",  "PFW": "Purdue Fort Wayne",  "PITT": "Pittsburgh",  "PORT": "Portland",
    "PRE": "Presbyterian",  "PRIN": "Princeton",  "PROV": "Providence",  "PRST": "Portland St.",
    "PSU": "Penn St.",  "PUR": "Purdue",  "PV": "Prairie View A&M",  "QUC": "Queens University",
    "QUIN": "Quinnipiac",  "RICH": "Richmond",  "RMU": "Robert Morris",  "RUTG": "Rutgers",
    "SAM": "Samford",  "SBON": "St. Bonaventure",  "SCAR": "South Carolina",  "SCST": "South Carolina St.",
    "SCU": "Santa Clara",  "SDAK": "South Dakota",  "SDST": "South Dakota St.",  "SDSU": "San Diego St.",
    "SEA": "Seattle",  "SELA": "Southeastern Louisiana",  "SEMO": "Southeast Missouri St.",  "SF": "San Francisco",
    "SFA": "Stephen F. Austin",  "SHSU": "Sam Houston",  "SHU": "Sacred Heart",  "SIE": "Siena",
    "SIU": "Southern Illinois",  "SIUE": "SIU Edwardsville",  "SJSU": "San Jose St.",  "SJU": "St. John's",
    "SLU": "Saint Louis",  "SMC": "Saint Mary's",  "SMU": "SMU",  "SOU": "Southern University",
    "SPC": "Saint Peter's",  "STAN": "Stanford",  "STET": "Stetson",  "STON": "Stony Brook",
    "SUU": "Southern Utah",  "TARL": "Tarleton St.",  "TCU": "TCU",  "TEM": "Temple",
    "TENN": "Tennessee",  "TLSA": "Tulsa",  "TNST": "Tennessee St.",  "TNTC": "Tennessee Tech",
    "TOL": "Toledo",  "TOWS": "Towson",  "TROY": "Troy",  "TTU": "Texas Tech",
    "TULN": "Tulane",  "TXAM": "Texas A&M",  "TXSO": "Texas Southern",  "TXST": "Texas St.",
    "UALR": "Little Rock",  "UCD": "UC Davis",  "UCF": "UCF",  "UCLA": "UCLA",
    "UCRV": "UC Riverside",  "UCSB": "UC Santa Barbara",  "UCSD": "UC San Diego",  "UGA": "Georgia",
    "UIC": "UIC",  "UK": "Kentucky",  "ULM": "Louisiana-Monroe",  "UMBC": "UMBC",
    "UMES": "Maryland-Eastern Shore",  "UMKC": "Kansas City",  "UNA": "North Alabama",  "UNC": "North Carolina",
    "UNCA": "UNC Asheville",  "UNCG": "UNC Greensboro",  "AKR": "Akron",  "UNCW": "UNC Wilmington",  "UNI": "Northern Iowa",  "UNLV": "UNLV",
    "UNM": "New Mexico",  "UNT": "North Texas",  "URI": "Rhode Island",  "USA": "South Alabama",
    "USC": "USC",  "USD": "San Diego",  "USF": "South Florida",  "USI": "Southern Indiana",
    "USM": "Southern Miss",  "USU": "Utah St.",  "UTAH": "Utah",  "UTEP": "UTEP",
    "UTM": "Tennessee-Martin",  "UTRGV": "UT Rio Grande Valley",  "UTSA": "UTSA",  "UTU": "Utah Tech",
    "UVA": "Virginia",  "UWFL": "West Florida",  "UWGA": "West Georgia",  "VALP": "Valparaiso",
    "VAN": "Vanderbilt",  "VCU": "VCU",  "VILL": "Villanova",  "VMI": "VMI",
    "VT": "Virginia Tech",  "WAKE": "Wake Forest",  "WEB": "Weber St.",  "WIN": "Winthrop",
    "WIS": "Wisconsin",  "WIU": "Western Illinois",  "WM": "William & Mary",  "WMU": "Western Michigan",
    "WRST": "Wright St.",  "WSU": "Washington St.",  "WVU": "West Virginia",  "WYO": "Wyoming",
    "XAV": "Xavier",  "YALE": "Yale",  "YSU": "Youngstown St.",
}

# BettingPros team code -> Kalshi code (name-joined; see module docstring for the join rule).
NCAAB_BP_TO_KALSHI = {
    "AAMU": "AAMU",  "AC": "AC",  "AFA": "AFA",  "ALA": "ALA",  "ALBY": "ALBY",  "ALCN": "ALCN",
    "AMCC": "AMCC",  "AMER": "AMER",  "APP": "APP",  "ARIZ": "ARIZ",  "ARK": "ARK",  "ARMY": "ARMY",
    "ARPB": "ARPB",  "ARST": "ARST",  "ASU": "ASU",  "AUB": "AUB",  "BALL": "BALL",  "BAY": "BAY",
    "BC": "BC",  "BEL": "BEL",  "BGSU": "BGSU",  "BRAD": "BRAD",  "BRWN": "BRWN",  "BRY": "BRY",
    "BSU": "BSU",  "BUCK": "BUCK",  "BUT": "BUT",  "BYU": "BYU",  "CAL": "CAL",  "CAMP": "CAMP",
    "CAN": "CAN",  "CARK": "CARK",  "CBU": "CBU",  "CCSU": "CCSU",  "CHAR": "CHAR",  "CHAT": "CHAT",
    "CHS": "CHS",  "CHSO": "CHSO",  "CLEM": "CLEM",  "CLEV": "CLEV",  "CLMB": "CLMB",  "CMU": "CMU",
    "COFC": "COFC",  "COLG": "COLG",  "COLO": "COLO",  "CONN": "CONN",  "COOK": "COOK",  "COPP": "COPP",
    "COR": "COR",  "CP": "CP",  "CREI": "CREI",  "CSB": "CSB",  "CSU": "CSU",  "DART": "DART",
    "DAV": "DAV",  "DAY": "DAY",  "DEL": "DEL",  "DEP": "DEP",  "DESU": "DSU",  "DET": "DET",
    "DREX": "DREX",  "DRKE": "DRKE",  "DUKE": "DUKE",  "DUQ": "DUQ",  "ECU": "ECU",  "EKY": "EKY",
    "ELON": "ELON",  "ETAM": "ETAM",  "ETSU": "ETSU",  "EVAN": "EVAN",  "FAIR": "FAIR",  "FAMU": "FAMU",
    "FAU": "FAU",  "FDU": "FDU",  "FGCU": "FGCU",  "FIU": "FIU",  "FLA": "FLA",  "FOR": "FOR",
    "FRES": "FRES",  "FSU": "FSU",  "FUR": "FUR",  "GASO": "GASO",  "GAST": "GAST",  "GB": "GB",
    "GC": "GC",  "GMU": "GMU",  "GT": "GT",  "GTWN": "GTWN",  "GW": "GW",  "HALL": "HALL",
    "HARV": "HARV",  "HC": "HC",  "HCU": "HCU",  "HOF": "HOF",  "HOU": "HOU",  "HOW": "HOW",
    "HP": "HP",  "IDHO": "IDHO",  "ILL": "ILL",  "ILST": "ILST",  "IND": "IND",  "INST": "INST",
    "IONA": "IONA",  "IOWA": "IOWA",  "ISU": "ISU",  "IUIN": "IUIN",  "IW": "IW",  "JKST": "JKST",
    "JMU": "JMU",  "JOES": "JOES",  "JVST": "JVST",  "KENN": "KENN",  "KENT": "KENT",  "KSU": "KSU",
    "KU": "KU",  "L-IL": "LCHI",  "L-MD": "LMD",  "LAS": "LAS",  "LBSU": "LBSU",  "LEH": "LEH",
    "LIB": "LIB",  "LINW": "LINW",  "LIP": "LIP",  "LIU": "LIU",  "LMU": "LMU",  "MARQ": "MARQ",
    "MASS": "MASS",  "MASSL": "MASSL",  "MD": "MD",  "ME": "ME",  "MEM": "MEM",  "MER": "MER",
    "MHU": "MHU",  "MIA": "MIA",  "MICH": "MICH",  "MILW": "MILW",  "MINN": "MINN",  "MISS": "MISS",
    "MONM": "MONM",  "MONT": "MONT",  "MORG": "MORG",  "MRMK": "MRMK",  "MRST": "MRST",  "MSM": "MSM",
    "MSST": "MSST",  "MSU": "MSU",  "MTST": "MTST",  "MTU": "MTU",  "MURR": "MURR",  "MVSU": "MVSU",
    "NAU": "NAU",  "NAVY": "NAVY",  "NCAT": "NCAT",  "NCCU": "NCCU",  "NCST": "NCST",  "ND": "ND",
    "NDSU": "NDSU",  "NE": "NE",  "NEB": "NEB",  "NEOM": "NEOM",  "NEV": "NEV",  "NHC": "NHC",
    "NIAG": "NIAG",  "NICH": "NICH",  "NJIT": "NJIT",  "NKU": "NKU",  "NMSU": "NMSU",  "NORF": "NORF",
    "NW": "NW",  "OAK": "OAK",  "ODU": "ODU",  "OHIO": "OHIO",  "OKLA": "OKLA",  "OKST": "OKST",
    "ORE": "ORE",  "ORST": "ORST",  "ORU": "ORU",  "OSU": "OSU",  "PAC": "PAC",  "PENN": "PENN",
    "PEPP": "PEPP",  "PFW": "PFW",  "PITT": "PITT",  "PORT": "PORT",  "PRE": "PRE",  "PRIN": "PRIN",
    "PROV": "PROV",  "PRST": "PRST",  "PSU": "PSU",  "PUR": "PUR",  "PV": "PV",  "QUC": "QUC",
    "QUIN": "QUIN",  "RICH": "RICH",  "RMU": "RMU",  "RUTG": "RUTG",  "SAM": "SAM",  "SBON": "SBON",
    "SCAR": "SCAR",  "SCST": "SCST",  "SCU": "SCU",  "SDAK": "SDAK",  "SDST": "SDST",  "SDSU": "SDSU",
    "SEA": "SEA",  "SELA": "SELA",  "SEMO": "SEMO",  "SF": "SF",  "SFA": "SFA",  "SHSU": "SHSU",
    "SHU": "SHU",  "SIE": "SIE",  "SIU": "SIU",  "SIUE": "SIUE",  "SJSU": "SJSU",  "SJU": "SJU",
    "SLU": "SLU",  "SMC": "SMC",  "SMU": "SMU",  "SOU": "SOU",  "SPC": "SPC",  "STAN": "STAN",
    "STET": "STET",  "STON": "STON",  "SUU": "SUU",  "TARL": "TARL",  "TCU": "TCU",  "TEM": "TEM",
    "TENN": "TENN",  "TLSA": "TLSA",  "TNST": "TNST",  "TNTC": "TNTC",  "TOL": "TOL",  "TOWS": "TOWS",
    "TROY": "TROY",  "TTU": "TTU",  "TULN": "TULN",  "TXAM": "TXAM",  "TXSO": "TXSO",  "TXST": "TXST",
    "UALR": "UALR",  "UCD": "UCD",  "UCF": "UCF",  "UCLA": "UCLA",  "UCRV": "UCRV",  "UCSB": "UCSB",
    "UCSD": "UCSD",  "UGA": "UGA",  "UIC": "UIC",  "UK": "UK",  "ULM": "ULM",  "UMBC": "UMBC",
    "UMES": "UMES",  "UMKC": "UMKC",  "UNA": "UNA",  "UNC": "UNC",  "UNCA": "UNCA",  "UNCW": "UNCW",
    "UNI": "UNI",  "UNLV": "UNLV",  "UNM": "UNM",  "UNT": "UNT",  "URI": "URI",  "USA": "USA",
    "USC": "USC",  "USD": "USD",  "USF": "USF",  "USI": "USI",  "USM": "USM",  "USU": "USU",
    "UTAH": "UTAH",  "UTEP": "UTEP",  "UTM": "UTM",  "UTRGV": "UTRGV",  "UTSA": "UTSA",  "UTU": "UTU",
    "UVA": "UVA",  "UWFL": "UWFL",  "UWGA": "UWGA",  "VALP": "VALP",  "VAN": "VAN",  "VCU": "VCU",
    "VILL": "VILL",  "VMI": "VMI",  "VT": "VT",  "WAKE": "WAKE",  "WEB": "WEB",  "WIN": "WIN",
    "WIS": "WIS",  "WIU": "WIU",  "WM": "WM",  "WMU": "WMU",  "WRST": "WRST",  "WSU": "WSU",
    "WVU": "WVU",  "WYO": "WYO",  "XAV": "XAV",  "YALE": "YALE",  "YSU": "YSU",
}


def kalshi_name(sport: str, code: str) -> str | None:
    """Kalshi code -> the school name Kalshi itself prints for it, or None (code outside the census)."""
    return (NCAAF_KALSHI_NAMES if sport == "ncaaf" else NCAAB_KALSHI_NAMES).get(str(code or "").upper())


def bp_to_kalshi(sport: str, bp_code: str) -> str | None:
    """BettingPros team code -> Kalshi code, or None: the game is not priced (fail closed, never a guess)."""
    table = NCAAF_BP_TO_KALSHI if sport == "ncaaf" else NCAAB_BP_TO_KALSHI
    return table.get(str(bp_code or "").strip().upper())


def name_to_kalshi(sport: str, name: str) -> str | None:
    """A verbatim Kalshi school name -> its code (live-feed name resolution); any other spelling returns None."""
    names = NCAAF_KALSHI_NAMES if sport == "ncaaf" else NCAAB_KALSHI_NAMES
    inv = _NAME_INV.setdefault(sport, {v: k for k, v in names.items()})
    return inv.get(str(name or "").strip())


_NAME_INV: dict = {}

# ---- parse sets: the census minus the minimum drop set that restores unique blob splits (see docstring) ----
# Dropped codes stay in the census, the name tables and the crosswalks above; they are excluded ONLY from
# event-ticker blob parsing, so a game whose ticker needs one fails closed (REJ_AMBIGUOUS) rather than being
# split by a guess. Minimum-drop proof and live cost are measured in the PR packet (2026-11-10).
NCAAF_PARSE_DROPPED_CODES = frozenset({"ME", "ND"})                       # Maine, Notre Dame
NCAAB_PARSE_DROPPED_CODES = frozenset({"AMER", "CSB", "EVAN", "ILL", "MASS", "SF"})
#                                                    American, Cal State Bakersfield, Evansville, Illinois, UMass, San Francisco
NCAAF_PARSE_CODES = NCAAF_KALSHI_CODES - NCAAF_PARSE_DROPPED_CODES
NCAAB_PARSE_CODES = NCAAB_KALSHI_CODES - NCAAB_PARSE_DROPPED_CODES


def parse_codes(sport: str) -> frozenset:
    """The code set event-ticker blobs for this sport are split against (census minus parse-dropped codes)."""
    return NCAAF_PARSE_CODES if sport == "ncaaf" else NCAAB_PARSE_CODES
