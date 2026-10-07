package org.example.ticket.performance.domain;

import jakarta.persistence.*;
import lombok.*;

@Entity
@Table(name = "performance")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@AllArgsConstructor
@Builder
public class Performance {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    @Column(nullable = false, length = 160)
    private String title;
}
