---
title: 24-7 Python Background Runner
emoji: ⚡
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---

# 🚀 24/7 Python Runner on Hugging Face Spaces

Yeh template aapke Python scripts, bots, scrapers, aur APIs ko **Hugging Face Spaces par 24/7 hamesha live** rakhne ke liye design kiya gaya hai.

---

## 🛠️ Setup Guide (Sirf 2 Minute Ka Kaam):

### Step 1: Hugging Face par Space Banayein
1. [huggingface.co](https://huggingface.co) par jayein (free account banayein agar nahi hai).
2. Top right me profile icon par click karein ➡️ **"New Space"**.
3. Settings select karein:
   * **Space name:** e.g., `my-python-runner`
   * **License:** `mit` (ya koi bhi)
   * **Select the Space SDK:** 👉 **Docker** (Blank select karein)
   * **Space hardware:** Free (CPU basic - 2 vCPU, 16GB RAM)
   * **Visibility:** Public ya Private (dono chalega)
4. **"Create Space"** par click karein.

---

### Step 2: Files Upload Karein
Aapke Space create hone ke baad **Files** tab me jayein aur yeh 3 files upload/create karein:
1. `Dockerfile`
2. `requirements.txt`
3. `app.py`

*(Ya fir direct `git push` kar sakte hain apne local machine se!)*

---

### Step 3: Done! 🎉
Hugging Face automatically container build karega aur **30 second ke andar live kar dega**:
* Aapko ek permanent free HTTPS domain mil jayega: `https://<your-username>-my-python-runner.hf.space`
* Is URL ko browser me open karenge toh live **Uptime, RAM, CPU, aur Logs Dashboard** dikhega.
* Background me aapka task 24/7 bina kisi timeout ke chalta rahega!
