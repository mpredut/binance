from pushbullet import Pushbullet
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

####MYLIB
from apikeys import PUSHBULLET_API_KEY
pb = Pushbullet(PUSHBULLET_API_KEY)

# Configurare SMTP
SMTP_SERVER = "smtp.googl.com"  # Exemplu: smtp.gmail.com
SMTP_PORT = 587  # Portul pentru TLS
SMTP_USERNAME = "predut111@google.com"
SMTP_PASSWORD = "preSuiram1!"
TO_EMAIL = "predut111@google.com"  # Adresa de email la care să trimiți alertele

def send_push_notification(title, message):
    """Trimite o notificare pe Android prin Pushbullet."""
    pb.push_note(title, message)

def send_email(subject, body, to_email):
    """Trimite un email."""
    msg = MIMEMultipart()
    msg['From'] = SMTP_USERNAME
    msg['To'] = to_email
    msg['Subject'] = subject

    msg.attach(MIMEText(body, 'plain'))

    server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
    server.starttls()
    server.login(SMTP_USERNAME, SMTP_PASSWORD)
    text = msg.as_string()
    server.sendmail(SMTP_USERNAME, to_email, text)
    server.quit()

def check_alert(condition, message):
    """Verifică condiția și trimite o notificare dacă aceasta este adevărată."""
    if condition:
        # Trimite notificare push pe Android
        send_push_notification("Alertă Trading", message)
        
        # Trimite email
        send_email(
            subject="Alertă Trading",
            body=message,
            to_email=TO_EMAIL
        )
