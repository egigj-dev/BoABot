"""Verified attribution extracted from the BoA business-rate source table."""
BUSINESS_RATE_SOURCE_DIMENSIONS = {"product_category": "OVERDRAFT", "currency": "LEK", "rate_type": "nominal_fixed"}
BBSH = "Banka e Bashkuar e Shqipërisë"
BIS = "Banka Intesa SanPaolo e Shqipërisë"
BPC = "Banka Procredit"
BUSINESS_RATE_ATTRIBUTION = {
 27: {"business_size":"small", "maturity_band":(0,12), "bank_values":((BBSH,"9.00"),(BIS,"8.00"),(BPC,"10.50"))},
 28: {"business_size":"small", "maturity_band":(13,24), "bank_values":((BBSH,"8.00"),)},
 29: {"business_size":"small", "maturity_band":(25,36), "bank_values":((BBSH,"8.00"),)},
 31: {"business_size":"medium", "maturity_band":(0,12), "bank_values":((BBSH,"9.00"),(BPC,"7.50"))},
 32: {"business_size":"medium", "maturity_band":(13,24), "bank_values":((BBSH,"9.00"),)},
 33: {"business_size":"medium", "maturity_band":(25,36), "bank_values":((BBSH,"8.00"),)},
 35: {"business_size":"large", "maturity_band":(0,12), "bank_values":((BBSH,"8.00"),(BPC,"6.00"))},
 36: {"business_size":"large", "maturity_band":(13,24), "bank_values":((BBSH,"8.00"),)},
 37: {"business_size":"large", "maturity_band":(25,36), "bank_values":((BBSH,"8.60"),)},
}
