from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse
import cv2
import easyocr
import json
import re
from PIL import Image, ImageDraw
from selenium import webdriver
from selenium.webdriver.common.by import By

app = FastAPI()

# EasyOCR reader 객체 생성 (한글 및 영어 지원)
reader = easyocr.Reader(['ko', 'en'], gpu=True)

def process_image(img):
    image = cv2.imread(img)
    if image is None:
        raise Exception(f"이미지를 읽을 수 없습니다: {img}")

    results = reader.readtext(image)
    extracted_text = []

    img = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(img)

    for detection in results:
        bbox = detection[0]
        text = detection[1]
        extracted_text.append(text)
        # 박스 그리기 생략...

    def extract_business_numbers(text_list):
        business_number_pattern = re.compile(r'\b\d{3}-?\d{2}-?\d{5}\b')
        return [match for text in text_list for match in business_number_pattern.findall(text)]

    def extract_store_names(text_list):
        store_name_pattern = re.compile(r'(매장명|상호명|회사명|업체명|가맣점명|[상싱성][호오]|[회훼]사)\s*[:;：]?\s*([^\s)]+(?:\s*\S*)*?[점]\s*?\S*)')
        return [match[1] for text in text_list for match in store_name_pattern.findall(text)]

    def is_valid_date(date_str):
        """날짜 형식이 유효한지 검사하는 함수"""
        parts = re.split(r'[-/.]', date_str)  # '-', '/', '.' 구분자로 사용
        if len(parts) == 3:
            # YYYY-MM-DD 형식 체크
            if len(parts[0]) == 4 and len(parts[1]) == 2 and len(parts[2]) == 2:
                year, month, day = map(int, parts)
                return 2000 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31
            # DD/MM/YYYY 형식 체크
            elif len(parts[0]) == 2 and len(parts[1]) == 2 and len(parts[2]) == 4:
                day, month, year = map(int, parts)
                return 2000 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31
            # MM-DD-YY 형식 체크
            elif len(parts[0]) == 2 and len(parts[1]) == 2 and len(parts[2]) == 2:
                month, day, year = map(int, parts)
                year += 2000 if year < 100 else 0
                return 2000 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31
        return False
        
    def extract_transaction_date(json_data):
        # 다양한 날짜 형식을 허용하는 정규식
        date_pattern = re.compile(
            r'([거기][래레][일닐]|[결겔]제[일닐]|거[래레]일시|[결겔]제날짜|날짜|일자)?\s*[:;：]?\s*'
            r'(\d{4}[-/.]\d{2}[-/.]\d{2}|\d{2}[-/.]\d{2}[-/.]\d{4}|\d{2}[-/.]\d{2}[-/.]\d{2})'  # YYYY-MM-DD, DD/MM/YYYY, MM-DD-YY 형식
        )
    
        extracted_dates = []
    
        for text in json_data:  # json_data가 문자열 목록이라고 가정
            matches = date_pattern.findall(text)
    
            for match in matches:
                date_part = match[1]  # 날짜 부분만 추출
    
                # 유효한 날짜만 추가
                if is_valid_date(date_part):
                    extracted_dates.append(date_part)
    
        return extracted_dates

    
    def extract_max_number(text_list):
        all_numbers = []
        for text in text_list:
            numbers = re.findall(r'\d{1,3}(?:,\d{3})*', text)
            numbers = [int(num.replace(',', '')) for num in numbers]
            all_numbers.extend(numbers)
        return max(all_numbers, default=0) if all_numbers else None

    info_dict = {
        "사업자번호": extract_business_numbers(extracted_text),
        "가맹점명": extract_store_names(extracted_text),
        "거래일시": extract_transaction_date(extracted_text),
        "금액": extract_max_number(extracted_text) 
    }
    
    return info_dict

def extract_category_keywords(business_numbers):
    
    driver = webdriver.Chrome()

    category_keywords_dict = {}

    for business_number in business_numbers:
        business_number_clean = business_number.replace("-", "")
        
        address = 'https://bizno.net/article/' + business_number_clean
        print(f"접속 중인 URL: {address}")

        driver.get(address)

        # 상호명 추출
        try:
            shop_name = driver.find_element(By.XPATH, '/html/body/section[2]/div/div/div[1]/div[1]/div/div[1]/div/a/h1').text
        except NoSuchElementException:
            shop_name = "상호명 없음"  
        
        # category_keywords 추출
        category_keywords = None
        try:
            element = driver.find_element(By.XPATH, '/html/body/section[2]/div/div/div[1]/div[1]/div/table/tbody/tr[2]/td')
            if all(label in element.text for label in ["대분류", "중분류", "소분류", "세분류", "세세분류"]):
                category_keywords = element.text
        except NoSuchElementException:
            pass
        
        if not category_keywords:
            try:
                element = driver.find_element(By.XPATH, '/html/body/section[2]/div/div/div[1]/div[1]/div/table/tbody/tr[4]/td')
                if all(label in element.text for label in ["대분류", "중분류", "소분류", "세분류", "세세분류"]):
                    category_keywords = element.text
            except NoSuchElementException:
                pass
        
        if category_keywords:
            print(f"업태: {category_keywords}")
        
            category_dict = {
                "상호명": shop_name,
                "대분류": re.search(r"대분류\s*:\s*(.*?)(?=\s*중분류|$)", category_keywords),
                "중분류": re.search(r"중분류\s*:\s*(.*?)(?=\s*소분류|$)", category_keywords),
                "소분류": re.search(r"소분류\s*:\s*(.*?)(?=\s*세분류|$)", category_keywords),
                "세분류": re.search(r"세분류\s*:\s*(.*?)(?=\s*세세분류|$)", category_keywords),
                "세세분류": re.search(r"세세분류\s*:\s*(.*)", category_keywords)
            }

            for key in category_dict:
                if isinstance(category_dict[key], re.Match):
                    category_dict[key] = category_dict[key].group(1).strip()
                
                elif category_dict[key] is not None:
                    category_dict[key] = category_dict[key].strip()
            
            category_keywords_dict[business_number] = category_dict
        else:
            print(f"사업자 번호 {business_number}에 대한 정보를 찾을 수 없습니다.")

    driver.quit()

    return category_keywords_dict

@app.post("/extract")
async def extract_data(file: UploadFile = File(...)):
    # 이미지 파일 저장
    image_path = f"temp_{file.filename}"
    with open(image_path, "wb") as f:
        f.write(await file.read())

    # 이미지 처리
    info_dict = process_image(image_path)
    business_numbers = info_dict.get("사업자번호", [])

    # 사업자 번호로 카테고리 키워드 추출
    category_keywords = extract_category_keywords(business_numbers)

    # 최종 결과 구성
    result = {
        "OCR_결과": info_dict,
        "카테고리_키워드": category_keywords
    }

    return JSONResponse(content=result)
