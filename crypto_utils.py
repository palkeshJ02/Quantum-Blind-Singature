import random

def get_msb_position(n: int) -> int:
    return n.bit_length() - 1

def gf2_polynomial_modulo(message: int, poly: int) -> int:
    msg_len = message.bit_length()
    poly_len = poly.bit_length()
    if msg_len < poly_len:
        return message
        
    poly_shifted = poly << (msg_len - poly_len)
    
    while message.bit_length() >= poly_len:
        message ^= poly_shifted
        shift_amount = msg_len - message.bit_length()
        poly_shifted >>= shift_amount
        msg_len = message.bit_length()
        
    return message

def calculate_digest(message_bits: str, irreducible_poly_bits: str) -> str:
    m_int = int(message_bits, 2)
    p_int = int(irreducible_poly_bits, 2)
    n = get_msb_position(p_int)
    m_shifted = m_int << n
    digest_int = gf2_polynomial_modulo(m_shifted, p_int)
    return format(digest_int, f'0{n}b')

def otp_xor(data_bits: str, key_bits: str) -> str:
    
    if len(data_bits) != len(key_bits):
        raise ValueError(f"OTP requires equal lengths: Data({len(data_bits)}) vs Key({len(key_bits)})")
    
    data_int = int(data_bits, 2)
    key_int = int(key_bits, 2)
    result_int = data_int ^ key_int
    
    return format(result_int, f'0{len(data_bits)}b')

def derive_signing_key(k_ab: str, k_ac: str) -> str:
    return otp_xor(k_ab, k_ac)

def key_to_str(key_list):
    return ''.join(str(b) for b in key_list)

def generate_random_poly(degree=4):
    poly = "1"
    for _ in range(degree - 1): poly += str(random.randint(0, 1))
    poly += "1"
    return poly

def blind_digest(digest, key):
    return otp_xor(digest, key[:len(digest)])



