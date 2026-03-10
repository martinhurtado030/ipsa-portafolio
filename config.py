# IPSA Constituents — Santiago Stock Exchange (Yahoo Finance tickers use .SN suffix)
IPSA_CONSTITUENTS = {
    "AGUAS-A.SN":    "Aguas Andinas S.A.",
    "BESALCO.SN":    "Besalco S.A.",
    "BSANTANDER.SN": "Banco Santander-Chile",
    "BCI.SN":        "Banco de Credito e Inversiones",
    "CHILE.SN":      "Banco de Chile",
    "CAP.SN":        "CAP S.A.",
    "CCU.SN":        "Compania Cervecerias Unidas S.A.",
    "CENCOSUD.SN":   "Cencosud S.A.",
    "CMPC.SN":       "Empresas CMPC S.A.",
    "COLBUN.SN":     "Colbun S.A.",
    "CONCHATORO.SN": "Vina Concha y Toro S.A.",
    "COPEC.SN":      "Empresas Copec S.A.",
    "ECL.SN":        "Enel Chile S.A.",
    "ENELAM.SN":     "Enel Americas S.A.",
    "ENTEL.SN":      "Empresa Nacional de Telecomunicaciones S.A.",
    "FALABELLA.SN":  "S.A.C.I. Falabella",
    "HABITAT.SN":    "AFP Habitat S.A.",
    "IAM.SN":        "Inversiones Aguas Metropolitanas S.A.",
    "ILC.SN":        "Inversiones La Construccion S.A.",
    "ITAUCL.SN":     "Itau CorpBanca",
    "LTM.SN":        "LATAM Airlines Group S.A.",
    "MALLPLAZA.SN":  "Mall Plaza S.A.",
    "PARAUCO.SN":    "Parque Arauco S.A.",
    "QUINENCO.SN":   "Quinenco S.A.",
    "RIPLEY.SN":     "Ripley Corp S.A.",
    "SALFACORP.SN":  "SalfaCorp S.A.",
    "SECURITY.SN":   "Grupo Security S.A.",
    "SK.SN":         "Sigdo Koppers S.A.",
    "SMU.SN":        "SMU S.A.",
    "SONDA.SN":      "Sonda S.A.",
    "SQM-B.SN":      "Sociedad Quimica y Minera de Chile S.A. (Serie B)",
    "VAPORES.SN":    "Compania Sud Americana de Vapores S.A.",
}

# Sector mapping for IPSA constituents
IPSA_SECTORS = {
    "AGUAS-A":   "Utilities",
    "BESALCO":   "Industrials",
    "BSANTANDER":"Financials",
    "BCI":       "Financials",
    "CHILE":     "Financials",
    "CAP":       "Materials",
    "CCU":       "Consumer Staples",
    "CENCOSUD":  "Consumer Disc.",
    "CMPC":      "Materials",
    "COLBUN":    "Utilities",
    "CONCHATORO":"Consumer Staples",
    "COPEC":     "Energy",
    "ECL":       "Utilities",
    "ENELAM":    "Utilities",
    "ENTEL":     "Telecom",
    "FALABELLA": "Consumer Disc.",
    "HABITAT":   "Financials",
    "IAM":       "Utilities",
    "ILC":       "Financials",
    "ITAUCL":    "Financials",
    "LTM":       "Industrials",
    "MALLPLAZA": "Real Estate",
    "PARAUCO":   "Real Estate",
    "QUINENCO":  "Industrials",
    "RIPLEY":    "Consumer Disc.",
    "SALFACORP": "Industrials",
    "SECURITY":  "Financials",
    "SK":        "Industrials",
    "SMU":       "Consumer Staples",
    "SONDA":     "Technology",
    "SQM-B":     "Materials",
    "VAPORES":   "Industrials",
}

# Macro tickers for Morning Briefing
MACRO_TICKERS = {
    "Copper (USD/lb)": "HG=F",
    "USD/CLP":         "CLP=X",
    "S&P 500":         "^GSPC",
    "IPSA":            "^IPSA",
}

# IPSA index ticker for alpha calculations
IPSA_TICKER = "^IPSA"

# Timezone
CHILE_TZ = "America/Santiago"

# Market hours (CLT, 24h)
MARKET_OPEN_HOUR   = 9
MARKET_OPEN_MINUTE = 30
MARKET_CLOSE_HOUR  = 17

# Technical signal thresholds
SMA_STRETCH_THRESHOLD = 15.0   # % from 200-SMA to flag as extreme

# Valuation reference P/E (Chilean market average)
REFERENCE_PE = 14.0
