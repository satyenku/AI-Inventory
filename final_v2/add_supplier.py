import sqlite3

def add_mock_supplier():
    conn = sqlite3.connect('inventory.db')
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO suppliers (supplier_name, contact_person, phone, email, address) 
        VALUES ('TechFlow Suppliers Inc.', 'Satyen Ku', '555-0199', 'satyen@techflow.com', '123 Innovation Drive, Silicon Valley, CA')
    """)
    conn.commit()
    conn.close()
    print("Supplier Added Successfully!")

if __name__ == '__main__':
    add_mock_supplier()
