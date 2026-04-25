from app.loaders.deterministic import identify_csv_model

headers = [
    "id,anrede,vorname,nachname,email,telefon,einheit_id,eigentuemer_id,mietbeginn,mietende,kaltmiete,nk_vorauszahlung,kaution,iban,bic,sprache",
    "id,anrede,vorname,nachname,email,telefon,einheit_id,eigentuemer_id,mietbeginn,mietende,kaltmiete,nk_vorauszahlung,kaution,iban,bic,sprache\r\n",
    "id,anrede,vorname,nachname,email,telefon,einheit_id,eigentuemer_id,mietbeginn,mietende,kaltmiete,nk_vorauszahlung,kaution,iban,bic,sprache\n",
]

for h in headers:
    model = identify_csv_model(h)
    print(f"Header: {repr(h)} -> Model: {model}")
