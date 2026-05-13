from sequence.topology.router_net_topo import RouterNetTopo
from sequence.network_management.network_manager import NetworkManager
from sequence.components.circuit import Circuit
import random
import time
from crypto_utils import otp_xor,calculate_digest, generate_random_poly, blind_digest, key_to_str

network_config = "topology.json"

def set_parameters(topology: RouterNetTopo):
    MEMO_FREQ = 2e3
    MEMO_EXPIRE = 0 
    MEMO_EFFICIENCY = 1
    MEMO_FIDELITY = 0.9
    for node in topology.get_nodes_by_type(RouterNetTopo.QUANTUM_ROUTER):
        memory_array = node.get_components_by_type("MemoryArray")[0]
        memory_array.update_memory_params("frequency", MEMO_FREQ)
        memory_array.update_memory_params("coherence_time", MEMO_EXPIRE)
        memory_array.update_memory_params("efficiency", MEMO_EFFICIENCY)
        memory_array.update_memory_params("raw_fidelity", MEMO_FIDELITY)
        
    DETECTOR_EFFICIENCY = 0.9
    DETECTOR_COUNT_RATE = 5e7
    DETECTOR_RESOLUTION = 100
    for node in topology.get_nodes_by_type(RouterNetTopo.BSM_NODE):
        bsm = node.get_components_by_type("SingleAtomBSM")[0]
        bsm.update_detectors_params("efficiency", DETECTOR_EFFICIENCY)
        bsm.update_detectors_params("count_rate", DETECTOR_COUNT_RATE)
        bsm.update_detectors_params("time_resolution", DETECTOR_RESOLUTION)
        
    # set entanglement swapping parameters
    SWAP_SUCC_PROB = 0.90
    SWAP_DEGRADATION = 0.99
    for node in topology.get_nodes_by_type(RouterNetTopo.QUANTUM_ROUTER):
        node.network_manager.protocol_stack[1].set_swapping_success_rate(SWAP_SUCC_PROB)
        node.network_manager.protocol_stack[1].set_swapping_degradation(SWAP_DEGRADATION)
        
    # set quantum channel parameters
    ATTENUATION = 1e-5
    QC_FREQ = 1e11
    for qc in topology.get_qchannels():
        qc.attenuation = ATTENUATION
        qc.frequency = QC_FREQ

def get_pairs(node, other_name):
    return [info for info in node.resource_manager.memory_manager
            if info.remote_node == other_name]


def e91_qkd(node1, node2):
    alice_infos = [info for info in node1.resource_manager.memory_manager if info.remote_node == node2.name]
    bob_infos = [info for info in node2.resource_manager.memory_manager if info.remote_node == node1.name]

    pair_count = min(len(alice_infos), len(bob_infos))
    
    if pair_count == 0:
        print("No entangled pairs available.")
        return [], []

    qm_alice = node1.timeline.quantum_manager
    qm_bob = node2.timeline.quantum_manager

    bases = ["Z", "X"]
    alice_bases = []
    bob_bases = []
    alice_results = []
    bob_results = []

    for i in range(pair_count):
        mem_a = alice_infos[i].memory
        mem_b = bob_infos[i].memory

        a_basis = random.choice(bases)
        b_basis = random.choice(bases)
        alice_bases.append(a_basis)
        bob_bases.append(b_basis)

        # Alice Measures
        circ_a = Circuit(1)
        if a_basis == "X":
            circ_a.h(0)
        circ_a.measure(0)
        samp_a = [random.random()]
        meas_dict_a = qm_alice.run_circuit(circ_a, [mem_a.qstate_key], samp_a)
        alice_results.append(meas_dict_a[mem_a.qstate_key])

        # Bob Measures
        circ_b = Circuit(1)
        if b_basis == "X":
            circ_b.h(0)
        circ_b.measure(0)
        samp_b = [random.random()]
        meas_dict_b = qm_bob.run_circuit(circ_b, [mem_b.qstate_key], samp_b)
        bob_results.append(meas_dict_b[mem_b.qstate_key])

    sifted_alice = []
    sifted_bob = []

    for i in range(pair_count):
        if alice_bases[i] == bob_bases[i]:
            sifted_alice.append(alice_results[i])
            sifted_bob.append(bob_results[i])

    errors = sum([1 for a, b in zip(sifted_alice, sifted_bob) if a != b])
    qber = errors / len(sifted_alice) if len(sifted_alice) > 0 else 0

    print(f"\n=== QKD RESULTS ({node1.name} <-> {node2.name}) ===")
    print("Pairs measured:", pair_count, "| Sifted key:", len(sifted_alice), "| Simulated QBER:", round(qber, 4))

    return sifted_alice, sifted_bob

def run_qkd_pair(network_config, src_name, dst_name):
    network_topo = RouterNetTopo(network_config)
    tl = network_topo.get_timeline()
    set_parameters(network_topo)

    src = dst = None
    for node in network_topo.get_nodes_by_type(RouterNetTopo.QUANTUM_ROUTER):
        if node.name == src_name: src = node
        elif node.name == dst_name: dst = node

    nm = src.network_manager
    nm.request(dst_name, start_time=1e12, end_time=10e12, memory_size=25, target_fidelity=0.9)
    tl.init()
    tl.run()

    return e91_qkd(src, dst)

def generate_fixed_key(network_config, src_name, dst_name, target_len=8):
    final_key = []
    while len(final_key) < target_len:
        alice_key, _ = run_qkd_pair(network_config, src_name, dst_name)
        if len(alice_key) == 0: continue
        final_key.extend(alice_key)
        print(final_key)
    return final_key[:target_len]


class ClassicalChannel:
    def __init__(self, latency_ms=50):
        self.latency_ms = latency_ms

    def transmit(self, sender_name, receiver_name, packet_type, payload, receiver_callback):
        print(f"\n[NETWORK] Routing '{packet_type}' from {sender_name} to {receiver_name}...")
        time.sleep(self.latency_ms / 1000.0) 
        print(f"[NETWORK] Delivered to {receiver_name}. Processing...")
        receiver_callback(payload)

class BobNode:
    def __init__(self, channel: ClassicalChannel, K_ab, K_bc):
        self.name = "Bob"
        self.channel = channel
        self.K_ab = K_ab
        self.K_bc = K_bc
        self.stored_digest = None 
        self.blinded_digest = None
        self.charlie_ref = None

    def initiate_protocol(self, message_bits, target_alice, target_charlie):
        self.charlie_ref = target_charlie
        print(f"\n--- {self.name} INITIATES QBS ---")
        poly_bits = generate_random_poly(8)
        self.stored_digest = calculate_digest(message_bits, poly_bits)
        
        # Blinding: Dig' = Dig XOR K_bc
        self.blinded_digest = blind_digest(self.stored_digest, self.K_bc)
        print(f"[{self.name}] Created Blinded Digest (Dig'): {self.blinded_digest}")
        
        # 1. PRE-STAGE: Send K_ab and Dig' to Charlie for his later verification
        payload_to_charlie = {"K_ab": self.K_ab, "Dig_p": self.blinded_digest}
        self.channel.transmit(self.name, target_charlie.name, "Bob's Key & Blinded Digest", payload_to_charlie, target_charlie.receive_bob_data)

        # 2. INITIATE: Send Dig' to Alice for signing
        self.channel.transmit(self.name, target_alice.name, "Blinded Digest", self.blinded_digest, target_alice.receive_blinded_digest)
    def receive_charlie_data(self, payload):
        print(f"\n--- {self.name} VERIFICATION PHASE ---")
        Sig_p = payload["Sig_p"]
        K_ac_from_charlie = payload["K_ac"]
        
        # Unblind the signature: Sig = Sig' XOR K_bc
        Sig = otp_xor(Sig_p, self.K_bc[:len(Sig_p)])
        
        # Deduce signing key: X_b = K_ab XOR K_ac
        X_b = otp_xor(self.K_ab, K_ac_from_charlie)[:len(Sig)]
        
        # Decrypt to expected digest: Dig_exp = Sig XOR X_b
        recovered_digest = otp_xor(Sig, X_b)
        
        print(f"[{self.name}] Original Digest:  {self.stored_digest}")
        print(f"[{self.name}] Expected Digest:  {recovered_digest}")
        
        if recovered_digest == self.stored_digest:
            print(f"[{self.name}] ✅ Signature VERIFIED. Informing Charlie to finalize.")
            self.channel.transmit(self.name, self.charlie_ref.name, "Verification Decision", "YES", self.charlie_ref.receive_bob_decision)
        else:
            print(f"[{self.name}] ❌ Signature FAILED.")
            self.channel.transmit(self.name, self.charlie_ref.name, "Verification Decision", "NO", self.charlie_ref.receive_bob_decision)

class AliceNode:
    def __init__(self, channel: ClassicalChannel, K_ab, K_ac, charlie_ref):
        self.name = "Alice"
        self.channel = channel
        self.K_ab = K_ab
        self.K_ac = K_ac
        self.charlie_ref = charlie_ref

    def receive_blinded_digest(self, payload):
        Dig_p = payload
        print(f"[{self.name}] Received Dig'. Signing...")
        
        # Alice signs: Sig' = Dig' XOR X_a (where X_a = K_ab XOR K_ac)
        X_a = otp_xor(self.K_ab, self.K_ac)[:len(Dig_p)]
        Sig_p = otp_xor(Dig_p, X_a)
        
        # 2. Transmit blinded signature to Charlie
        self.channel.transmit(self.name, self.charlie_ref.name, "Blinded Signature", Sig_p, self.charlie_ref.receive_blinded_signature)

class CharlieNode:
    def __init__(self, channel: ClassicalChannel, K_bc, K_ac, bob_ref):
        self.name = "Charlie (CA)"
        self.channel = channel
        self.K_bc = K_bc
        self.K_ac = K_ac
        self.bob_ref = bob_ref 
        
        # Network state cache
        self.received_Sig_p = None
        self.received_Kab = None
        self.received_Dig_p = None

    def receive_bob_data(self, payload):
        self.received_Kab = payload["K_ab"]
        self.received_Dig_p = payload["Dig_p"]
        print(f"[{self.name}] Received K_ab and Dig' from Bob.")

    def receive_blinded_signature(self, payload):
        self.received_Sig_p = payload
        print(f"[{self.name}] Received Sig' from Alice.")
        
        # 4. Once Charlie has Sig', he sends it and his K_ac to Bob
        payload_to_bob = {"Sig_p": self.received_Sig_p, "K_ac": self.K_ac}
        self.channel.transmit(self.name, self.bob_ref.name, "Charlie's Key & Blinded Signature", payload_to_bob, self.bob_ref.receive_charlie_data)

    def receive_bob_decision(self, payload):
        if payload == "YES":
            print(f"\n--- {self.name} FINAL VERIFICATION ---")
            
            # Unblind the signature: Sig = Sig' XOR K_bc
            Sig = otp_xor(self.received_Sig_p, self.K_bc[:len(self.received_Sig_p)])
            
            # Deduce signing key: X_c = K_ab XOR K_ac
            X_c = otp_xor(self.received_Kab, self.K_ac)[:len(Sig)]
            
            # Decrypt to expected digest: Dig_exp = Sig XOR X_c
            expected_digest = otp_xor(Sig, X_c)
            
            # Calculate actual digest: Dig_act = Dig' XOR K_bc
            actual_digest = otp_xor(self.received_Dig_p, self.K_bc[:len(self.received_Dig_p)])
            
            print(f"[{self.name}] Expected Digest: {expected_digest}")
            print(f"[{self.name}] Actual Digest:   {actual_digest}")
            
            if expected_digest == actual_digest:
                print(f"[{self.name}] ✅ Signature VERIFIED. Protocol successfully completed.")
            else:
                print(f"[{self.name}] ❌ Signature FAILED.")
        else:
            print(f"[{self.name}] Protocol aborted by Bob.")


if __name__ == "__main__":
    print(">>> PHASE 1: QUANTUM KEY DISTRIBUTION (QKD)")
    key_ab_raw = generate_fixed_key(network_config, "alice", "bob", target_len=8)
    print("Alice<--->Bob -> Key : ", key_ab_raw)
    key_ac_raw = generate_fixed_key(network_config, "alice", "charlie", target_len=8)
    print("Alice<--->Charlie -> Key : ", key_ac_raw)
    key_bc_raw = generate_fixed_key(network_config, "bob", "charlie", target_len=8)
    print("Charlie<--->Bob -> Key : ", key_bc_raw)

    K_ab = key_to_str(key_ab_raw)
    K_ac = key_to_str(key_ac_raw)
    K_bc = key_to_str(key_bc_raw)

    print("\n--- FINAL QUANTUM KEYS ---")
    print("Alice–Bob:     ", K_ab)
    print("Alice–Charlie: ", K_ac)
    print("Bob–Charlie:   ", K_bc)

    print("\n>>> PHASE 2: DISTRIBUTED QUANTUM BLIND SIGNATURE (QBS)")
    
    classical_network = ClassicalChannel(latency_ms=100)
    
    # Initialize nodes with only their specific shared keys
    bob_node = BobNode(classical_network, K_ab=K_ab, K_bc=K_bc)
    charlie_node = CharlieNode(classical_network, K_bc=K_bc, K_ac=K_ac, bob_ref=bob_node)
    alice_node = AliceNode(classical_network, K_ab=K_ab, K_ac=K_ac, charlie_ref=charlie_node)
    
    message_to_sign = "101010011"
    
    # Pass Charlie's reference to Bob so he can transmit his key and digest
    bob_node.initiate_protocol(message_to_sign, target_alice=alice_node, target_charlie=charlie_node)