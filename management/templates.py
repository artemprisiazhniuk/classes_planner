# Events notification templates
def get_schedule_template(company_name="Template Company"):
    return """Liebe {tag} Kurs BesucherInnen,\n\nhier ist der Zeitplan für {period}.\n\n{schedule}\n\nMit freundlichen Grüßen,\n""" + company_name

def get_update_template(company_name="Template Company"):
    return """Liebe {tag} Kurs BesucherInnen,\n\nDiese Terminezeiten wurden geändert.\n\n{schedule}\n\nMit freundlichen Grüßen,\n""" + company_name 

def get_delete_template(company_name="Template Company"):
    return """Liebe {tag} Kurs BesucherInnen,\n\nDiese Termine wurden abgesagt.\n\n{schedule}\n\nMit freundlichen Grüßen,\n""" + company_name