package org.example.ticket.reservation.service;

import org.example.ticket.reservation.dto.ReservationRequest;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;

@Component
public class ReservationRequestHasher {
    public String hash(ReservationRequest request) {
        String canonical = request.performanceTimeId() + ":" + request.seatIds().stream()
                .sorted().map(String::valueOf).reduce((a, b) -> a + "," + b).orElse("");
        try {
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(canonical.getBytes(StandardCharsets.UTF_8))
            );
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256을 사용할 수 없습니다.", exception);
        }
    }
}
