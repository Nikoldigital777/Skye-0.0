import asyncio
import json
import numpy as np
import sounddevice as sd
from rpi_ws281x import PixelStrip, Color
import os
import requests
import time
from picamera2 import Picamera2
import cv2
import speech_recognition as sr
import logging
import webbrowser
import bluetooth
import threading
import RPi.GPIO as GPIO
import mss  # For screen capturing

# ==================== Configuration ====================
TAVUS_API_KEY = 'your-tavus-api-key'
TAVUS_API_URL = "https://tavusapi.com/v2/conversations"
REPLICA_ID = "re8e740a42"
PERSONA_ID = "p24293d6"

WAKE_WORD = "hey skye"
FACETIME_TRIGGER = "let's facetime"
EXIT_PHRASE = "end call"

# Bluetooth Configuration
BLUETOOTH_ADDRESS = "XX:XX:XX:XX:XX:XX"  # Replace with MAC address of Seeed device

# Hall Sensor Configuration
HALL_SENSOR_PIN = 17

# LED Strip Configuration
LED_COUNT = 70
LED_PIN = 21
LED_FREQ_HZ = 800000
LED_DMA = 10
LED_BRIGHTNESS = 50
LED_INVERT = False
LED_CHANNEL = 0

# Initialization
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

camera = Picamera2()
camera.configure(camera.create_preview_configuration(main={"size": (640, 480)}))
camera.start()

recognizer = sr.Recognizer()

# Global flags
video_capture_running = False
motor_running = False
current_rotation = 0  # To keep track of motor rotations
TOTAL_ROTATIONS = 10  # Adjust as needed

# ==================== Helper Functions ====================

def recognize_speech():
    """Recognizes speech using Google API"""
    with sr.Microphone() as source:
        logger.info("Listening...")
        audio = recognizer.listen(source, timeout=5, phrase_time_limit=5)
    try:
        return recognizer.recognize_google(audio).lower()
    except (sr.UnknownValueError, sr.RequestError) as e:
        logger.error(f"Speech recognition error: {e}")
        return ""

async def listen_for_phrase(phrase, timeout=10):
    """Listens for a specific phrase within the given timeout"""
    end_time = time.time() + timeout
    while time.time() < end_time:
        text = recognize_speech()
        if phrase in text:
            return True
        await asyncio.sleep(0.1)
    return False

def create_tavus_conversation():
    """Create a conversation via the Tavus API"""
    headers = {
        "x-api-key": TAVUS_API_KEY,
        "Content-Type": "application/json"
    }
    data = {
        "replica_id": REPLICA_ID,
        "persona_id": PERSONA_ID,
        "conversation_name": "POV Display Conversation",
        "properties": {
            "enable_recording": True,
            "max_call_duration": 600,
        }
    }
    try:
        response = requests.post(TAVUS_API_URL, headers=headers, json=data)
        response.raise_for_status()
        return response.json().get('conversation_url')
    except requests.RequestException as e:
        logger.error(f"Failed to create Tavus conversation: {e}")
        return None

async def tavus_cvi_meeting(conversation_url):
    """Handles Tavus CVI meeting and starts video/motor sync"""
    global video_capture_running, motor_running
    logger.info("Starting Tavus CVI meeting...")
    
    # Open the conversation URL in a web browser
    webbrowser.open(conversation_url)
    
    # Start video capture and processing
    video_capture_running = True
    video_thread = threading.Thread(target=capture_and_process_video)
    video_thread.start()
    
    # Start motor synchronization
    motor_running = True
    motor_thread = threading.Thread(target=motor_sync)
    motor_thread.start()
    
    try:
        while True:
            if await listen_for_phrase(EXIT_PHRASE, timeout=0.1):
                logger.info("Exit phrase detected. Ending call.")
                break
    except Exception as e:
        logger.error(f"Error in Tavus CVI meeting: {e}")
    finally:
        # Stop video capture and motor
        video_capture_running = False
        motor_running = False
        video_thread.join()
        motor_thread.join()
        
        # Close the browser window
        os.system("pkill -f chromium")
        logger.info("Call ended.")

# ==================== Video Processing and POV Display ====================

def capture_browser_window():
    """Capture the screen where the Tavus video is displayed"""
    with mss.mss() as sct:
        monitor = {"top": 0, "left": 0, "width": 640, "height": 480}
        screenshot = sct.grab(monitor)
        return np.array(screenshot)

def capture_and_process_video():
    """Captures and processes video for the POV display"""
    global video_capture_running
    while video_capture_running:
        frame = capture_browser_window()
        processed_frame = process_frame_for_pov(frame)
        send_to_seeed_bluetooth(processed_frame)
        time.sleep(0.01)  # Adjust for the desired frame rate

def process_frame_for_pov(frame):
    """Convert captured frame into POV-ready format"""
    processed_frame = cv2.resize(frame, (64, 64))  # Adjust resolution as needed
    return processed_frame.tobytes()

# ==================== Motor Synchronization ====================

def motor_sync():
    """Synchronizes motor rotation with POV frame display"""
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(HALL_SENSOR_PIN, GPIO.IN)
    
    global motor_running, current_rotation
    while motor_running:
        if GPIO.input(HALL_SENSOR_PIN) == GPIO.HIGH:
            current_rotation = (current_rotation + 1) % TOTAL_ROTATIONS
            send_next_frame_slice()
        time.sleep(0.001)  # Adjust according to motor speed

def send_next_frame_slice():
    """Send the appropriate frame slice based on motor rotation"""
    frame_slice = get_frame_slice(current_rotation)
    send_to_seeed_bluetooth(frame_slice)

# ==================== Bluetooth Communication ====================

def send_to_seeed_bluetooth(data):
    """Sends data to the Seeed device via Bluetooth"""
    sock = bluetooth.BluetoothSocket(bluetooth.RFCOMM)
    try:
        sock.connect((BLUETOOTH_ADDRESS, 1))
        sock.send(data)
    except bluetooth.BluetoothError as be:
        logger.error(f"Bluetooth error: {be}")
    except Exception as e:
        logger.error(f"Error sending data to Seeed: {e}")
    finally:
        sock.close()

# ==================== Graceful Shutdown ====================

def cleanup():
    """Graceful cleanup of resources"""
    global video_capture_running, motor_running
    video_capture_running = False
    motor_running = False
    camera.stop()
    GPIO.cleanup()
    logger.info("Cleanup complete. Exiting.")

# ==================== Main Loop ====================

async def main():
    """Main execution loop"""
    try:
        logger.info("POV Display system initialized. Say 'Hey Skye' to start.")

        while True:
            if await listen_for_phrase(WAKE_WORD):
                logger.info("Wake word detected. Awaiting further instructions...")

                if await listen_for_phrase(FACETIME_TRIGGER, timeout=10):
                    conversation_url = create_tavus_conversation()
                    if conversation_url:
                        await tavus_cvi_meeting(conversation_url)
                    else:
                        logger.error("Failed to create Tavus conversation. Please try again.")
                else:
                    logger.info(f"'{FACETIME_TRIGGER}' not detected. Returning to wake word detection.")
    except KeyboardInterrupt:
        logger.info("Program interrupted by user.")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
    finally:
        cleanup()

if __name__ == "__main__":
    asyncio.run(main())
